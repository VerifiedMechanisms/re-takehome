"""Restricted, fully logged OpenRouter chat client."""

from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import httpx

from .budget import BudgetLedger
from .events import EventLogger
from .models import ALLOWED_MODELS, PRICE_CEILINGS

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_REQUEST_BYTES = 2_000_000
MAX_OUTPUT_TOKENS = 32_000
REFUSED_BEFORE_GENERATION = frozenset({429})


class LLMPolicyError(ValueError):
    pass


class LLMCallError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMResponse:
    id: str
    model: str
    content: str
    reasoning: Any
    tool_calls: list[dict[str, Any]]
    finish_reason: str | None
    usage: Mapping[str, Any]
    raw: Mapping[str, Any]


class LLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        budget: BudgetLedger,
        events: EventLogger,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._api_key = api_key
        self._budget = budget
        self._events = events
        self._client = httpx.AsyncClient(
            headers={
                **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int = 4096,
        temperature: float | None = None,
        top_p: float | None = None,
        seed: int | None = None,
        stop: str | Sequence[str] | None = None,
        reasoning: Mapping[str, Any] | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: Any = None,
        timeout_s: float | None = None,
    ) -> LLMResponse:
        if not self._api_key:
            raise LLMCallError(
                "OPENROUTER_API_KEY is not configured; copy .env.example to .env"
            )
        if model not in ALLOWED_MODELS:
            raise LLMPolicyError(f"model {model!r} is not allowed")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= MAX_OUTPUT_TOKENS:
            raise LLMPolicyError(f"max_tokens must be between 1 and {MAX_OUTPUT_TOKENS}")
        if not messages:
            raise LLMPolicyError("messages must not be empty")
        self._validate_messages(messages)
        self._validate_reasoning(reasoning, max_tokens=max_tokens)
        price = PRICE_CEILINGS[model]

        body: dict[str, Any] = {
            "model": model,
            "messages": [dict(message) for message in messages],
            "max_tokens": max_tokens,
            "stream": False,
            # Applicants cannot opt into alternate endpoints, fallback models,
            # or providers that silently ignore requested fields.
            "provider": {
                "allow_fallbacks": True,
                "require_parameters": True,
                "max_price": {
                    "prompt": price.input_per_million,
                    "completion": price.output_per_million,
                },
            },
        }
        for key, value in (
            ("temperature", temperature),
            ("top_p", top_p),
            ("seed", seed),
            ("stop", stop),
            ("reasoning", dict(reasoning) if reasoning else None),
            ("tools", [dict(tool) for tool in tools] if tools else None),
            ("tool_choice", tool_choice),
        ):
            if value is not None:
                body[key] = value

        try:
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise LLMPolicyError(f"request is not JSON serializable: {exc}") from exc
        if len(encoded) > MAX_REQUEST_BYTES:
            raise LLMPolicyError(f"request exceeds {MAX_REQUEST_BYTES} bytes")

        # UTF-8 bytes are a safe upper bound on tokenizer tokens for admission.
        reserve_usd = (
            len(encoded) * price.input_per_million / 1_000_000
            + max_tokens * price.output_per_million / 1_000_000
        ) * 1.10
        reservation = self._budget.reserve(reserve_usd)
        call_id = uuid.uuid4().hex
        started = time.monotonic()
        self._events.emit(
            "llm_request",
            call_id=call_id,
            request=body,
            reserved_cost_usd=reserve_usd,
        )

        try:
            request_args: dict[str, Any] = {"content": encoded}
            if timeout_s is not None:
                request_args["timeout"] = timeout_s
            response = await self._client.post(OPENROUTER_URL, **request_args)
        except BaseException as exc:
            snapshot = self._budget.mark_unknown(reservation)
            self._events.emit(
                "llm_error",
                call_id=call_id,
                error_type=type(exc).__name__,
                message=str(exc),
                cost_status="unknown",
                budget=snapshot.__dict__,
                latency_ms=round((time.monotonic() - started) * 1000),
            )
            if isinstance(exc, httpx.HTTPError):
                raise LLMCallError(
                    f"OpenRouter request failed; spend is uncertain: {exc}"
                ) from exc
            raise

        latency_ms = round((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            error_body = response.text[:4000]
            if response.status_code in REFUSED_BEFORE_GENERATION:
                reported_cost = self._reported_cost(response)
                if reported_cost is None:
                    self._budget.release(reservation)
                    snapshot = self._budget.snapshot()
                    cost_status = "none"
                    detail = "the request was refused and reported no cost"
                else:
                    snapshot = self._budget.settle(reservation, reported_cost)
                    cost_status = "billed"
                    detail = f"the request was refused after reporting ${reported_cost:.6f}"
            else:
                snapshot = self._budget.mark_unknown(reservation)
                cost_status = "unknown"
                detail = "spend is uncertain"
            self._events.emit(
                "llm_error",
                call_id=call_id,
                status_code=response.status_code,
                message=error_body,
                cost_status=cost_status,
                budget=snapshot.__dict__,
                latency_ms=latency_ms,
            )
            raise LLMCallError(
                f"OpenRouter returned HTTP {response.status_code}; {detail}: "
                f"{error_body[:500]}"
            )

        try:
            data = response.json()
            usage = data["usage"]
            cost = self._coerce_cost(usage["cost"])
            if cost is None:
                raise TypeError("usage.cost is not numeric")
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            snapshot = self._budget.mark_unknown(reservation)
            self._events.emit(
                "llm_error",
                call_id=call_id,
                message=f"invalid success response: {exc}",
                response=data if "data" in locals() else response.text[:4000],
                cost_status="unknown",
                budget=snapshot.__dict__,
                latency_ms=latency_ms,
            )
            raise LLMCallError(f"OpenRouter response omitted required usage accounting: {exc}") from exc

        snapshot = self._budget.settle(reservation, cost)
        self._events.emit(
            "llm_response",
            call_id=call_id,
            response=data,
            actual_cost_usd=cost,
            budget=snapshot.__dict__,
            latency_ms=latency_ms,
        )
        return LLMResponse(
            id=str(data.get("id", "")),
            model=str(data.get("model", model)),
            content=str(message.get("content") or ""),
            reasoning=message.get("reasoning"),
            tool_calls=list(message.get("tool_calls") or []),
            finish_reason=choice.get("finish_reason"),
            usage=dict(usage),
            raw=data,
        )

    @staticmethod
    def _coerce_cost(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None

    @classmethod
    def _reported_cost(cls, response: httpx.Response) -> float | None:
        try:
            return cls._coerce_cost(response.json()["usage"]["cost"])
        except (ValueError, KeyError, TypeError):
            return None

    @staticmethod
    def _validate_messages(messages: Sequence[Mapping[str, Any]]) -> None:
        allowed_roles = {"system", "developer", "user", "assistant", "tool"}
        for index, message in enumerate(messages):
            if not isinstance(message, Mapping):
                raise LLMPolicyError(f"messages[{index}] must be an object")
            if message.get("role") not in allowed_roles:
                raise LLMPolicyError(f"messages[{index}] has an invalid role")
            # Multimodal inputs have separate pricing that a text-token
            # reservation cannot safely bound, so this harness is text-only.
            if not isinstance(message.get("content"), str):
                raise LLMPolicyError(f"messages[{index}].content must be text")

    @staticmethod
    def _validate_reasoning(
        reasoning: Mapping[str, Any] | None, *, max_tokens: int
    ) -> None:
        if reasoning is None:
            return
        allowed = {"effort", "max_tokens", "exclude", "enabled"}
        unknown = set(reasoning) - allowed
        if unknown:
            raise LLMPolicyError(f"unsupported reasoning fields: {sorted(unknown)}")
        if "effort" in reasoning and reasoning["effort"] not in {
            "none", "minimal", "low", "medium", "high", "xhigh", "max"
        }:
            raise LLMPolicyError("invalid reasoning effort")
        if "max_tokens" in reasoning:
            value = reasoning["max_tokens"]
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= max_tokens:
                raise LLMPolicyError("reasoning max_tokens must fit inside max_tokens")
