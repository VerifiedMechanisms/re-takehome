from __future__ import annotations

import json

import pytest

from re_harness.manifest import ManifestError, load_problem_set


def _problem_set(tmp_path, entry):
    problem = tmp_path / entry["id"]
    problem.mkdir()
    (problem / "problem.md").write_text("problem")
    (problem / "challenge.lean").write_text("theorem x : True := by sorry")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "set": "test", "problems": [entry]
    }))


def test_manifest_loads_sample_set():
    problem_set = load_problem_set(__import__("pathlib").Path("sample-problems"))
    assert len(problem_set.problems) == 16
    p09 = next(problem for problem in problem_set.problems if problem.id == "p09_imo1964")
    assert p09.theorem_names == ("p09_a", "p09_b")


def test_putnam_tasks_have_fixed_targets():
    problem_set = load_problem_set(__import__("pathlib").Path("sample-problems"))
    specs = {problem.id: problem for problem in problem_set.problems}

    p2020 = specs["putnam_2020_a2"]
    assert p2020.definition_names == ()
    _, p2020_challenge = problem_set.paths(p2020)
    p2020_source = p2020_challenge.read_text(encoding="utf-8")
    assert "putnam_2020_a2_solution" not in p2020_source
    assert "= 4 ^ k" in p2020_source

    p2018 = specs["putnam_2018_a1"]
    assert p2018.definition_names == ()
    _, p2018_challenge = problem_set.paths(p2018)
    p2018_source = p2018_challenge.read_text(encoding="utf-8")
    assert "putnam_2018_a1_solution" not in p2018_source
    for pair in (
        "(673, 1358114)",
        "(674, 340033)",
        "(1009, 2018)",
        "(2018, 1009)",
        "(340033, 674)",
        "(1358114, 673)",
    ):
        assert pair in p2018_source


def test_numeric_answer_must_be_a_definition(tmp_path):
    _problem_set(tmp_path, {
        "id": "p", "theorem_names": ["x"],
        "definition_names": [], "numeric_answer_names": ["answer"],
    })
    with pytest.raises(ManifestError):
        load_problem_set(tmp_path)


def test_problem_files_may_not_be_symlinks(tmp_path):
    _problem_set(tmp_path, {
        "id": "p", "theorem_names": ["x"],
        "definition_names": [], "numeric_answer_names": [],
    })
    challenge = tmp_path / "p" / "challenge.lean"
    target = tmp_path / "target.lean"
    target.write_text("x")
    challenge.unlink()
    challenge.symlink_to(target)
    with pytest.raises(ManifestError):
        load_problem_set(tmp_path)
