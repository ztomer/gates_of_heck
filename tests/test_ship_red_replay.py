"""A ship's red is read whole and re-run before it is bisected (lib/ship_red_cause.py,
lib/ship_full_run.py, GOH.SUITEREDEARLY). In memory: git, the probe and the filer are given.

  * the red's rows at any indent, a test id with a space whole, a Rust red beside pytest's;
  * a test the probed land does not have is no red of that land;
  * HEAD is re-run and red again before any older land is probed;
  * the red is of the commit its log says it ran on, never of a HEAD that moved since.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import cov_risk
import full_run
import ship_full_run
import ship_red_cause
from ship_red_cause import GREEN, NO_VERDICT, RED

T, SPACED = "tests/t.py::test_a", "tests/t.py::test_b[git restore --worktree a.txt]"
HEAD_LINE = "✗ [diff-coverage] 3 of 9 changed executable lines uncovered (base x, GOH_CHANGE_BASE):"


@pytest.mark.parametrize("indent", [4, 6, 12])
def test_the_uncovered_rows_are_read_at_any_indent(indent: int) -> None:
    pad = " " * indent
    log = "\n".join([
        f"{pad[4:]}{HEAD_LINE}", f"{pad}src/a.rs:2  x();", f"{pad}src/two words.rs:13  ",
        f"{pad}src/b.rs:7", f"{pad}cover each, or name an unreachable one with its reason",
        f"{pad}src/c.rs:1  not a row of the red",
    ])  # fmt: skip
    want = [("src/a.rs", 2, "x();"), ("src/two words.rs", 13, ""), ("src/b.rs", 7, "")]
    assert ship_red_cause.uncovered(log) == want


def test_a_spaced_test_id_is_read_whole_and_a_cut_list_of_it_adds_no_other() -> None:
    """The ship of 16e31932 (2026-11-18): its first list cut the id at the space."""
    log = (
        f"    ERROR {SPACED.split(' ')[0]}\n        ERROR {SPACED}\n"
        f"    FAILED {T} - AssertionError: assert 1 == 0\n    ERROR tests/broken.py - ImportError\n"
    )
    assert sorted(ship_red_cause.failed_tests(log)) == sorted([T, SPACED, "tests/broken.py"])


def test_a_rust_red_beside_pytests_is_probed_by_the_full_run() -> None:
    red = f"    FAILED {T} - AssertionError\n"
    assert ship_red_cause.failed_tests(red) == [T]
    said = "TESTS FAILED under coverage"
    for rust in ("    test roadmap::tests::a ... FAILED\n", f"✗ x: {said}: roadmap::tests::a \n"):
        assert ship_red_cause.failed_tests(rust + red) == [], rust
    for pytests in (f"✗ external: {said} (1 red):\n", f"✗ x: {said}: the run names none -- see\n"):
        assert ship_red_cause.failed_tests(pytests + red) == [T], pytests


def _pytest_of(monkeypatch, has: dict[str, int]) -> list[list[str]]:
    """pytest over a tree of the tests HAS (their exit); one it has not is exit 4."""
    ran: list[list[str]] = []

    def run(wt: Path, ids: list[str], max_s: str) -> tuple[int, str]:
        ran.append(list(ids))
        gone = [n for n in ids if n not in has]
        if gone:
            return 4, f"ERROR: not found: /lane/wt/{gone[0].split('[')[0]}\n(no match)\n"
        return max(has[n] for n in ids), ""

    monkeypatch.setattr(ship_red_cause, "_pytest", run)
    return ran


def test_a_test_the_probed_land_has_not_is_no_red_of_it(monkeypatch) -> None:
    ran = _pytest_of(monkeypatch, {T: 1})
    assert ship_red_cause.probe(Path("wt"), [SPACED, T], "9m") == RED
    assert ran == [[SPACED, T], [T]]
    _pytest_of(monkeypatch, {})
    assert ship_red_cause.probe(Path("wt"), [SPACED, T], "9m") == GREEN, "no test, no red"
    out = "✗ an xdist worker's collection failed: file or directory not found: tests/t.py::test_a\n"
    assert ship_red_cause.absent([T, "tests/u.py::test_a"], out) == [T]
    assert ship_red_cause.absent([T], "ERROR: not found: /lane/wt/tests/other.py::test_a\n") == []


def test_a_probe_that_ran_nothing_for_another_reason_is_still_no_verdict(monkeypatch) -> None:
    monkeypatch.setattr(ship_red_cause, "_pytest", lambda wt, ids, m: (4, "ERROR: usage: -n"))
    assert ship_red_cause.probe(Path("wt"), [T], "9m") == NO_VERDICT


LANDS = ["l0", "l1", "l2", "tip"]


def _bisect(monkeypatch, verdicts: dict[str, int], tests: list[str]) -> tuple[dict, list[str]]:
    asked: list[str] = []
    at = [""]

    def prepare(repo, wt, sha: str) -> str:
        at[0] = sha
        return ""

    def probe(*_a) -> int:
        asked.append(at[0])
        return verdicts[at[0]]

    def judge(repo, wt, sha: str, base, argv) -> int:
        at[0] = sha
        return probe()

    monkeypatch.setattr(ship_full_run, "last_ship", lambda repo, sha: "base")
    monkeypatch.setattr(ship_full_run, "lands", lambda repo, base, head: LANDS)
    monkeypatch.setattr(ship_full_run, "_judge", judge)
    monkeypatch.setattr(ship_full_run, "file_fault", lambda *a: "filed X")
    monkeypatch.setattr(full_run, "prepare", prepare)
    monkeypatch.setattr(
        full_run, "_git", lambda *a: SimpleNamespace(stdout="subject", returncode=0)
    )
    monkeypatch.setattr(ship_red_cause, "probe", probe)
    return ship_full_run.bisect(Path("r"), Path("wt"), "tip", tests=tests), asked


@pytest.mark.parametrize("tests", [[T], []])
def test_head_is_rerun_before_any_older_land_is_probed(monkeypatch, tests) -> None:
    green = dict.fromkeys(LANDS, GREEN)
    b, asked = _bisect(monkeypatch, green, tests)  # red once: a flake
    assert asked == ["tip"] and b["flake"] and b["candidate"] == "tip" and not b["culprit"], b
    b, asked = _bisect(monkeypatch, {**green, "tip": NO_VERDICT}, tests)
    assert asked == ["tip"] and not b.get("flake") and b["fault"].startswith("not filed"), b
    b, asked = _bisect(monkeypatch, {**green, "l2": RED, "tip": RED}, tests)
    assert asked[0] == "tip" and b["culprit"] == "l2", (asked, b)
    b, asked = _bisect(monkeypatch, {**green, "tip": RED}, tests)
    assert b["culprit"] == "tip" and asked.count("tip") == 1, (asked, b)


RAN = "e8a77022586fae1a5781f7fc68dca19d845c276c"


def _red(tmp_path: Path, monkeypatch, log: str) -> tuple[dict, list[str]]:
    """after_red on a ship whose suite ran on RAN, main's HEAD a later land by now."""
    (tmp_path / "s.log").write_text(log)
    step = {"name": "suite", "status": "failed", "code": RED, "log": str(tmp_path / "s.log")}
    res = {"failed_step": "suite", "steps": [step], "repo": "r", "result": str(tmp_path / "r.json")}
    heads: list[str] = []

    def git(repo, *args: str) -> SimpleNamespace:
        whole = {"HEAD": "later", f"{RAN[:8]}^{{commit}}": RAN, f"{RAN}^{{commit}}": RAN}
        return SimpleNamespace(stdout=whole.get(args[-1], ""), returncode=0)

    def attribute(repo, head: str, lands, rows) -> list[dict]:
        heads.append(head)
        return [{"file": f, "line": n, "text": t, "commit": "", "land": ""} for f, n, t in rows]

    def bisect(repo, wt, head: str, **_kw) -> dict:
        heads.append(head)
        return {"culprit": "", "fault": "not filed"}

    monkeypatch.setattr(full_run, "_git", git)
    monkeypatch.setattr(ship_full_run, "last_ship", lambda repo, sha: "base")
    monkeypatch.setattr(ship_full_run, "lands", lambda repo, base, head: [head])
    monkeypatch.setattr(ship_full_run, "bisect", bisect)
    monkeypatch.setattr(ship_red_cause, "attribute", attribute)
    ship_full_run.after_red(res, lambda r: None)
    return res, heads


def test_the_red_is_of_the_commit_the_suite_ran_on(tmp_path, monkeypatch) -> None:
    """The ship handed 94ea25e3 waited for a land, its suite ran on e8a77022, and by the time the
    land lock was given back main could be anywhere."""
    for named in (RAN, RAN[:8]):  # the whole name, and the short one of a log kept before
        ran = f"ship full suite on {named} in /x/suite-wt, its diff coverage since 36f569fb\n"
        _res, heads = _red(tmp_path, monkeypatch, f"{ran}    FAILED {T} - AssertionError\n")
        assert heads == [RAN]
        res, heads = _red(tmp_path, monkeypatch, f"{ran}{HEAD_LINE}\n        src/a.rs:2  x();\n")
        assert heads == [RAN] and res["diffcov_red"]["lines"][0]["line"] == 2, res
    _res, heads = _red(tmp_path, monkeypatch, f"    FAILED {T} - AssertionError\n")
    assert heads == [], "a red naming no commit was attributed to whatever HEAD is"


def test_an_adopted_idle_runs_profile_is_kept_beside_the_ships_records(monkeypatch) -> None:
    kept: list[tuple[Path, Path]] = []
    monkeypatch.setattr(cov_risk, "keep_run", lambda wt, dest: kept.append((wt, dest)) or "")
    ship_full_run.keep_profile(Path("/landq/idle-suite/wt"), Path("/ship/goh/suite-wt"))
    assert kept == [(Path("/landq/idle-suite/wt"), Path("/ship/goh/covsrc.json"))]
