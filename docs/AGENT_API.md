# Applicant Agent API

Applicants edit `submission/agent.py`. The harness imports
`submission.agent:create_agent` unless `--agent module:factory` is provided for
local experiments.

## Inputs and result

```python
from re_harness import AgentResult, Problem, Services

class SubmissionAgent:
    async def solve(self, problem: Problem, services: Services) -> AgentResult:
        ...

def create_agent() -> SubmissionAgent:
    return SubmissionAgent()
```

`Problem` contains `id`, the Markdown `description`, the pristine Lean
`challenge`, and manifest `metadata`. Return one complete Lean file in
`AgentResult.solution`; optional metadata must be JSON serializable.

## Model calls

```python
response = await services.llm.complete(
    model="qwen/qwen3.5-flash-02-23",
    messages=[{"role": "user", "content": "..."}],
    max_tokens=4000,
    temperature=0.2,
    reasoning={"effort": "medium"},
)
text = response.content
```

The method also supports `top_p`, `seed`, `stop`, and ordinary local function
tool schemas. It does not accept OpenRouter plugins, model variants, fallback
models, provider URLs, or arbitrary request bodies. OpenRouter may retry another
provider for the same requested model, subject to the harness price ceiling.
Each call is logged and charged to the current problem's single ledger.

There are no hidden retries. Import and catch `re_harness.LLMCallError`
and decide explicitly whether
another request is worthwhile. A rate-limited request that reports no cost
leaves the ledger open for a retry. If an error response reports a cost, that
cost is charged. Transport failures, cancelled requests, malformed success
responses, and other uncertain failures close the ledger rather than risking an
unreported overspend. Once the ledger is closed, the next call raises
`BudgetAccountingError`.

## Lean feedback

```python
check = await services.lean.check_file(candidate_source)
if not check.accepted:
    compiler_feedback = check.messages
```

Send a complete file, including the original imports and theorem statements.
The service returns structured messages, `has_sorry`, `timed_out`, duration,
and whether a dead REPL was restarted. Every call branches from the same clean
Mathlib environment. Final grading does not trust this REPL: it uses a fresh
Comparator container.

## Checkpoints

```python
services.checkpoint(candidate_source, {"stage": "reviewed"})
```

Checkpoint after meaningful improvements. It atomically updates
`solution.lean`, so the outer runner can retain it if the agent process is
killed at the wall-clock deadline.

## Concurrency

`--n-workers` belongs to the outer runner and schedules different problems.
It does not alter the applicant's algorithm. Each problem has an isolated
agent process, budget, deadline, logs, and Lean container.
