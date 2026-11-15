"""swift_gate.sh --lint-only: the lint layer without a second build (GOH.SWIFTLINTONLY).

necrohand's full gate already builds, tests and measures its package; adding goh's Swift layer
ran all three again (~4 more minutes a run, NH.SWIFTLAYER a263da9) and warned that
GOH_SWIFT_COV_MIN was unset although the repo's own step holds the floor. --lint-only runs the
swiftlint stage and nothing after it; a lint finding is still red. Red first: on 2e38f15 the flag
was read as the repo path and the gate died in `cd`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import hermetic_env
from test_swift_gate_tests_warnings import SWIFT_GATE, _repo
from timing_bounds import patience


def _run(repo: Path, bin_dir: Path, tmp_path: Path, *args: str, **extra: str):
    env = hermetic_env(SWIFT_CALLS=str(tmp_path / "calls.txt"), **extra)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    r = subprocess.run(
        ["/bin/bash", str(SWIFT_GATE), *args, str(repo)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=patience(120),
    )
    calls = tmp_path / "calls.txt"
    return r, calls.read_text() if calls.exists() else ""


def test_lint_only_runs_the_lint_and_no_build(tmp_path):
    repo, bin_ = _repo(tmp_path)
    r, calls = _run(repo, bin_, tmp_path, "--lint-only")
    text = r.stdout + r.stderr
    assert r.returncode == 0, text
    assert "✓ swiftlint --strict" in text, text
    assert not [c for c in calls.splitlines() if c.split()[:1] in (["build"], ["test"])], calls
    assert "GOH_SWIFT_COV_MIN" not in text, text
    assert "all swift gates passed" in text, text


def test_lint_only_is_red_on_a_lint_finding(tmp_path):
    repo, bin_ = _repo(tmp_path)
    lint = bin_ / "swiftlint"
    head = lint.read_text().rsplit("exit 0\n", 1)[0]  # keeps the version answer
    lint.write_text(head + 'echo "A.swift:1:1: error: x (rule)"; exit 2\n')
    r, _ = _run(repo, bin_, tmp_path, "--lint-only")
    assert r.returncode != 0, r.stdout + r.stderr
    assert "swiftlint --strict failed" in r.stdout + r.stderr


def test_the_full_gate_still_builds(tmp_path):
    repo, bin_ = _repo(tmp_path)
    r, calls = _run(repo, bin_, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert any(c.startswith("build") for c in calls.splitlines()), calls
