# Rules

## The two models

Your system must use exactly these two models, via OpenRouter:

- **Model A:** `qwen/qwen3.5-flash-02-23`
- **Model B:** `openai/gpt-oss-120b`

- Use any sampling parameters, reasoning-effort settings, and prompting you like.
- Do not use `:free`, `:online`, or any other variant suffix, and do not use
  OpenRouter plugins or try to work around these restrictions.
- At run time your system may contact **only** `openrouter.ai`, and only with
  the two model IDs above. Web search and any other API are off limits. The
  harness may allow OpenRouter to use a different provider for the same model
  when the original provider is unavailable.
- There is no host Mathlib checkout to install or manage. Mathlib and all
  compiled artifacts are already inside the provided Lean container;
  generated Lean never runs on the host.
- Feel free to use the **key we sent you** for all runs (development and experiments). It
  carries a **$50 hard cap**, on us. The task is designed to fit within it;
  if you require additional credits, you may purchase them through your own
  OpenRouter account, though we are unable to reimburse that expense.
- You are welcome to use Claude, Codex, or ChatGPT to write your code;
  disclose it in the submission form.

## Scoring

- The holdout set has the **same format and grading** as `sample-problems`,
  roughly a dozen problems, though the difficulty may vary. Scoring is
  binary: **1 point per correct problem, 0 for incorrect**.
- A problem scores its point iff your `solution.lean` passes the **Lean
  comparator** (<https://github.com/leanprover/comparator>).
- If your system spends more than **$1.00** on a problem, that problem scores
  0, even if the proof is correct (the per-problem caps are enforced as
  described under How we judge).
- Spend means OpenRouter's actual returned `usage.cost`, summed across both
  models and every call for that problem. The provided API reserves a
  conservative maximum before each request to avoid a final-call overshoot.
- A rate-limited request with no reported cost does not consume budget. If an
  error response reports `usage.cost`, that cost is counted. Failures with
  uncertain spend close the problem's budget ledger.
- Use of each allowed model is recorded in `result.json` and reviewed. Model
  participation is reported separately and is not an additional mechanical
  condition for the one-point Comparator score.

## Conduct

- **No per-problem special-casing.** Your system must be problem-agnostic:
  it may not hardcode proofs or keep databases keyed to specific problems or
  statements. General few-shot examples and generic tactic libraries are fine.
- Building on open-source harnesses is allowed with attribution in the README;
  the two-model coordination layer must be your own work.
- We read transcripts, cross-check them against the key's usage ledger on
  OpenRouter, and may re-run any experiment you report.

## How we judge

- We clone your repo into the kit devcontainer and run, per problem:
  `OPENROUTER_API_KEY=<fresh key> VM_TIME_LIMIT_S=28800 VM_BUDGET_USD=1.00 python run.py --problems <holdout> --out <out-root>`
- **Caps per problem: 8 hours wall-clock and $1.00**, enforced by our
  wrapper (and the fresh key is model-locked to the pair). One run per problem,
  problems isolated: a crash on one problem zeroes only that problem.
- We re-verify every solution ourselves; anything your harness reports about
  itself is orientation only.
- The judging environment's network is restricted to `openrouter.ai`. Declare
  every Python dependency in `pyproject.toml`; we install them before the
  network lock. Lean, Mathlib, REPL, and Comparator come from the pinned image.
- `--n-workers` may schedule independent problems concurrently. Every problem
  still has its own time limit, dollar ledger, artifacts, and Lean container.
- The runner writes `<out-root>/<agent-name>/<timestamp>/`. Under that run
  directory we expect `<problem-id>/solution.lean`, `result.json`,
  `transcript.json`, and `events.jsonl`, plus root `run.json` and
  `summary.json`. Full LLM content and actual usage must be present; API keys
  must never appear.

- **Crash policy:** if your entrypoint fails in our environment we spend up to
  15 minutes on good-faith fixes; after that the mechanical score stands at
  whatever completed, and everything else (report, transcripts, code) is still
  graded. Passing `scripts/judge_check.sh` before you submit is your
  protection; do not skip it.
- **Simplicity preference.** We would rather run a simple design we can fully
  understand than a complicated one that scores marginally better. If two
  submissions score about the same, we prefer the simpler one.
