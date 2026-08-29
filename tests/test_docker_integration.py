from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import pytest

from re_harness.events import EventLogger
from re_harness.config import HarnessSettings
from re_harness.lean import LeanClient, compare_solution
from re_harness.manifest import ProblemSpec, load_problem_set
from re_harness.runner import run


IMAGE = os.environ.get("LEAN_INTEGRATION_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="set LEAN_INTEGRATION_IMAGE to test Docker")


def test_real_repl_valid_invalid_timeout_and_restart(tmp_path):
    async def exercise() -> None:
        client = LeanClient(
            image=IMAGE or "",
            events=EventLogger(tmp_path / "events.jsonl", problem_id="integration"),
            timeout_s=5,
        )
        try:
            valid = await client.check_file(
                "import Mathlib\n\ntheorem repl_ok : True := by exact True.intro"
            )
            assert valid.accepted
            invalid = await client.check_file(
                "import Mathlib\n\ntheorem repl_bad : False := by trivial"
            )
            assert not invalid.accepted
            runaway = await client.check_file(
                "import Mathlib\n\nrun_cmd IO.sleep 60000", timeout_s=1
            )
            assert runaway.timed_out
            recovered = await client.check_file(
                "import Mathlib\n\ntheorem repl_recovered : True := by exact True.intro"
            )
            assert recovered.accepted and recovered.container_restarted
        finally:
            client.close()

    asyncio.run(exercise())


def test_real_comparator_accepts_proof_and_rejects_statement_or_axiom():
    spec = ProblemSpec("smoke", ("smoke",), (), (), {})
    challenge = "import Mathlib\n\ntheorem smoke : True := by sorry\n"
    accepted = compare_solution(
        image=IMAGE or "",
        session_id=uuid.uuid4().hex,
        challenge=challenge,
        solution="import Mathlib\n\ntheorem smoke : True := by exact True.intro\n",
        spec=spec,
        timeout_s=180,
    )
    assert accepted.passed
    changed = compare_solution(
        image=IMAGE or "",
        session_id=uuid.uuid4().hex,
        challenge=challenge,
        solution="import Mathlib\n\ntheorem smoke : 1 = 1 := by rfl\n",
        spec=spec,
        timeout_s=180,
    )
    assert not changed.passed
    axiom = compare_solution(
        image=IMAGE or "",
        session_id=uuid.uuid4().hex,
        challenge=challenge,
        solution=(
            "import Mathlib\n\naxiom forbidden : False\n"
            "theorem smoke : True := False.elim forbidden\n"
        ),
        spec=spec,
        timeout_s=180,
    )
    assert not axiom.passed


@pytest.mark.parametrize(
    ("problem_id", "circular_solution"),
    [
        (
            "putnam_2020_a2",
            """import Mathlib

abbrev putnam_2020_a2_solution : ℕ → ℕ :=
  fun k => ∑ j ∈ Finset.Icc 0 k, 2 ^ (k - j) * Nat.choose (k + j) j

theorem putnam_2020_a2 (k : ℕ) :
    (∑ j ∈ Finset.Icc 0 k, 2 ^ (k - j) * Nat.choose (k + j) j) =
      putnam_2020_a2_solution k := by
  rfl
""",
        ),
        (
            "putnam_2018_a1",
            """import Mathlib

abbrev putnam_2018_a1_solution : Set (ℤ × ℤ) :=
  {p | (1 : ℚ) / p.1 + (1 : ℚ) / p.2 = (3 : ℚ) / 2018}

theorem putnam_2018_a1
    (a b : ℤ) (h : 0 < a ∧ 0 < b) :
    ((1 : ℚ) / a + (1 : ℚ) / b = (3 : ℚ) / 2018) ↔
      (⟨a, b⟩ ∈ putnam_2018_a1_solution) := by
  rfl
""",
        ),
    ],
)
def test_fixed_putnam_statements_reject_circular_solutions(
    problem_id: str, circular_solution: str
) -> None:
    problem_set = load_problem_set(Path("sample-problems"))
    spec = next(problem for problem in problem_set.problems if problem.id == problem_id)
    _, challenge_path = problem_set.paths(spec)

    verdict = compare_solution(
        image=IMAGE or "",
        session_id=uuid.uuid4().hex,
        challenge=challenge_path.read_text(encoding="utf-8"),
        solution=circular_solution,
        spec=spec,
        timeout_s=180,
    )

    assert not verdict.passed


def _one_problem_set(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "sample-problems" / "p01_linear"
    root = tmp_path / "problems"
    problem = root / "p01_linear"
    problem.mkdir(parents=True)
    for name in ("problem.md", "challenge.lean"):
        (problem / name).write_bytes((source / name).read_bytes())
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "set": "integration",
        "problems": [{
            "id": "p01_linear",
            "theorem_names": ["p01_linear"],
            "definition_names": [],
            "numeric_answer_names": [],
        }],
    }))
    return root


def test_real_worker_scores_checkpoint_after_agent_crash(tmp_path):
    problems = _one_problem_set(tmp_path)
    summary = run(
        problems_dir=problems,
        out_dir=tmp_path / "out-crash",
        settings=HarnessSettings("", IMAGE or "", 1.0, 60, 1, 5, 60, 10),
        agent="tests.fixture_agent:create_checkpoint_crash_agent",
    )
    assert summary["total_points"] == 1


def test_outer_hard_timeout_preserves_checkpoint_and_cleans_container(tmp_path):
    problems = _one_problem_set(tmp_path)
    out = tmp_path / "out-timeout"
    run(
        problems_dir=problems,
        out_dir=out,
        settings=HarnessSettings("", IMAGE or "", 1.0, 3, 1, 5, 20, 1),
        agent="tests.fixture_agent:create_hard_hang_agent",
    )
    result = json.loads((out / "p01_linear" / "result.json").read_text())
    assert result["status"] == "timed_out"
    assert "linarith" in (out / "p01_linear" / "solution.lean").read_text()
