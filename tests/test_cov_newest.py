"""What the newest complete run measured, asked at a ship's HEAD (lib/cov_newest.py,
GOH.SUITEREDEARLY).

Six of the last 14 red ship suites (2026-11-19 to 2026-11-23) were uncovered changed lines, 503 to
961 s in; the idle run of a7bd590f (2026-11-25) named 5 more and kept nothing. No git here: the
hunks, the tree and the kept profile are given.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import cov_newest
import cov_risk
import diffcov_carry

P, TAG, HEAD = "a" * 40, "v1", "head"
OLD, NEW, EXCLUDED = "src/old.rs", "src/new.rs", "src/cli.rs"
TEXT = {
    OLD: ["fn a() {", "x();", "}", "y();", "// note", "z();"],
    NEW: ["fn n() {", "// why", "}"],
    EXCLUDED: ["fn c() {}"],
}
# old.rs at the measured commit: lines 1-4 executable (2 uncovered), then HEAD put a line before 5.
PROFILE = {"sha": P, "files": {OLD: {"exec": [1, 2, 4], "own": [1], "cov": [1, 4]}}}
TAGGED = {
    OLD: (OLD, [(0, 0, 1, 6)]),
    NEW: ("", [(0, 0, 1, 3)]),
    EXCLUDED: (EXCLUDED, [(1, 1, 1, 1)]),
}
SINCE = {OLD: (OLD, [(5, 1, 5, 2)]), NEW: ("", [(0, 0, 1, 3)])}


@pytest.fixture
def profile(monkeypatch):
    """The kept profile, as a test sets it; git answers from the tables above."""
    kept = dict(PROFILE)

    def git(_repo, *args: str) -> str | None:
        if args[0] == "rev-list":
            return ""
        if args[0] == "show":
            return "\n".join(TEXT[args[1].partition(":")[2]])
        rel = args[2].partition(":")[2]
        return "" if rel in (OLD, EXCLUDED) or args[2] == f"{P}^{{commit}}" else None

    kept_at = SimpleNamespace(read_text=lambda: json.dumps(kept))
    monkeypatch.setattr(cov_risk, "summary_path", lambda repo: kept_at)
    monkeypatch.setattr(cov_risk, "hunks", lambda top, base, head: SINCE if base == P else TAGGED)
    monkeypatch.setattr(cov_risk, "allowed", lambda top: {(OLD, "allowed();")})
    monkeypatch.setattr(cov_newest, "_git", git)
    monkeypatch.setattr(diffcov_carry, "_git", git)
    monkeypatch.setattr(diffcov_carry, "_landed", lambda q, repo, order: [])
    return kept


def test_a_line_reads_as_the_run_measured_it_and_a_changed_one_not_at_all(profile) -> None:
    newest = cov_newest.Newest(Path("r"), HEAD)
    said = [newest.verdict(OLD, n) for n in range(1, 7)]
    assert said == ["covered", "uncovered", "not judged", "covered", "", ""], said
    assert newest.verdict(NEW, 1) == "", "a file new since was never measured"
    assert newest.verdict(EXCLUDED, 1) == "not judged", "left out of the run: no ship judges it"


def test_the_lines_a_run_measured_uncovered_are_named_before_the_suite(profile, monkeypatch):
    newest = cov_newest.Newest(Path("r"), HEAD)
    assert newest.uncovered(TAG) == [(OLD, 2, "x();")]
    why = diffcov_carry.ship_refusal(Path("r"), TAG, HEAD, None)
    assert why and f"{OLD}:2  x();  (measured at {P[:8]})" in why, why
    assert diffcov_carry.ship_refusal(Path("r"), TAG, HEAD, "tests/t.py covers it") is None
    monkeypatch.setattr(cov_risk, "allowed", lambda top: {(OLD, "x();")})
    assert diffcov_carry.ship_refusal(Path("r"), TAG, HEAD, None) is None


def test_the_lines_no_run_measured_are_named_and_refuse_nothing(profile, capsys) -> None:
    profile["files"] = {OLD: {"exec": [1, 2, 4], "own": [], "cov": [1, 2, 4]}}
    assert diffcov_carry.ship_refusal(Path("r"), TAG, HEAD, None) is None
    said = capsys.readouterr().err
    assert "3 changed Rust lines since v1 not measured by the newest profile (aaaaaaaa)" in said
    assert f"{NEW}:1,3\n" in said and f"{OLD}:6" in said, said  # no comment, no measured line


def test_no_profile_measures_nothing_and_names_every_changed_line(profile, capsys) -> None:
    profile["sha"] = ""
    assert cov_newest.Newest(Path("r"), HEAD).verdict(OLD, 2) == ""
    assert diffcov_carry.ship_refusal(Path("r"), TAG, HEAD, None) is None
    assert "not measured by any profile (none is kept)" in capsys.readouterr().err
