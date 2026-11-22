"""A shrunk or vanished swiftlint baseline entry refuses (GOH.SWIFTBASELINESTALE).

gates/swift_lint_baseline.py used to WARN and pass, so routines' baseline carried 49 entries the
debt could grow back into unseen. A ratchet's stale entry must FAIL (close-the-class), as
py_lint_ceiling's does, naming the exact command that re-records it; before crate::grace's day it
is the same lines as a warning, so the consumers have the week to re-record.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

HELPER = REPO_ROOT / "gates" / "swift_lint_baseline.py"
RERECORD = (
    "swiftlint lint --quiet --force-exclude --write-baseline .swiftlint-baseline.json"
    " $(git ls-files '*.swift')"
)
C27 = "Function should have complexity 10 or less; currently complexity is 27"
C26 = "Function should have complexity 10 or less; currently complexity is 26"


def entry(reason: str) -> dict:
    loc = {"character": 1, "file": "Sources/Sample.swift", "line": 3}
    return {"violation": {"location": loc, "reason": reason, "ruleIdentifier": "cc"}}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


def run_at(grace: str, root: Path, baseline: list, live: list) -> tuple[int, str]:
    (root / ".swiftlint-baseline.json").write_text(json.dumps(baseline))
    (root / "report.json").write_text(json.dumps(live))
    got = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--baseline",
            str(root / ".swiftlint-baseline.json"),
            "--report",
            str(root / "report.json"),
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
        env={**os.environ, "GOH_GRACE": grace},
    )
    return got.returncode, got.stdout + got.stderr


def live(root: Path, reason: str) -> dict:
    return {
        "file": str(root / "Sources" / "Sample.swift"),
        "line": 9,
        "reason": reason,
        "rule_id": "cc",
        "severity": "Error",
    }


def test_a_vanished_entry_refuses_with_the_re_record_command(root):
    rc, out = run_at("over", root, [entry(C27)], [])
    assert rc == 1, out
    assert "no longer occur" in out and "Sources/Sample.swift cc" in out, out
    assert RERECORD in out, out


def test_a_vanished_entry_only_warns_within_the_grace(root):
    rc, out = run_at("within", root, [entry(C27)], [])
    assert rc == 0, out
    assert "refused from 2026-11-23" in out and RERECORD in out, out


def test_a_shrunk_entry_refuses_naming_the_change_and_the_re_record_command(root):
    rc, out = run_at("over", root, [entry(C27)], [live(root, C26)])
    assert rc == 1, out
    assert "27 -> 26" in out and RERECORD in out, out
    assert "NEW lint" not in out, "a shrink is not a new finding"


def test_a_clean_baseline_still_passes(root):
    rc, out = run_at("over", root, [entry(C27)], [live(root, C27)])
    assert rc == 0, out
