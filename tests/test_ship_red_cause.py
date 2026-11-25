"""A ship's red names its cause without a bisect where the red already does (GOH.SHIPBISECTCOV).

2026-11-22: the ship of 84cbc55f went red at diff coverage at 02:35Z -- its log named ten
uncovered lines by file:line -- and its record said `failed`, `finished`. Yet its worker went on
bisecting the lands since the last ship, one full instrumented suite (~16 min) per step, holding
`.ship.lock`, so the next ship failed at once "at push" naming nothing but the lock. Now:

  * a diff coverage red never bisects: `git blame` names the land of each uncovered line, in the
    record (`diffcov_red`) and the red's tail, seconds after the red;
  * a red naming failing tests bisects on those tests alone, never the full instrumented suite;
  * while the worker bisects, the record says `bisecting` with its step, never `finished`;
  * a second ship finding the lock held names the holder's record and state; nothing starts.

The measurer is a DUMMY (tests/test_ship_cov_bisect.py): the fixture's own coverage gate.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path

import _goh_db_fixture as gdb
import test_ship_cov_bisect
from conftest import REPO_ROOT, commit_all, git, write
from test_ship import _calls
from test_ship_cov_bisect import _LOG, _RECORD, _ship
from test_ship_starve import _declare, _judged
from timing_bounds import patience

from lib import ship_status

shipped = test_ship_cov_bisect.shipped  # the fixture, by assignment

# A diff coverage red as gates/diff_cov.py prints it, its uncovered lines read from the tree's
# `cov.uncovered`; a failing test as pytest's summary names it when the tree has `cov.fail`.
DUMMY = (
    _LOG
    + """
if [ -s cov.uncovered ]; then
    echo "✗ [diff-coverage] 2 of 4 changed executable lines uncovered (base x, GOH_CHANGE_BASE):"
    cat cov.uncovered
    echo "    cover each, or name an unreachable one with its reason in diffcov_allow.json"
    exit 1
fi
if [ -e cov.fail ]; then echo "FAILED tests/t_mark.py::test_mark - AssertionError"; exit 1; fi
"""
    + _RECORD
)
# The test a failing-test red names, red while the tree has `cov.fail`: the narrow bisect's probe.
# It copies the ship's record as it stands at that moment (its goh.db row, GOH.STATEDB S2) beside
# the dummy's log.
MARK_TEST = """import json, os, subprocess, sys
from pathlib import Path

sys.path.insert(0, "@LIB@")
import ship_records


def test_mark():
    out = Path(os.environ["STAND_IN_LOG"]).parent / "probe-records"
    out.mkdir(exist_ok=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    rec = ship_records.get(os.environ["GOH_SHIP_RESULT"])
    (out / f"{head.strip()}.json").write_text(json.dumps(rec))
    assert not Path("cov.fail").exists()
""".replace("@LIB@", str(REPO_ROOT / "lib"))


def _declare_dummy(repo: Path) -> None:
    _declare(repo)
    write(repo, "gates/coverage_gate.sh", DUMMY.format(py=sys.executable, lib=REPO_ROOT / "lib"))
    write(repo, "tests/t_mark.py", MARK_TEST)
    commit_all(repo, "chore: the dummy measurer")


def _land(repo: Path, files: dict[str, str], subject: str) -> str:
    """One land, recorded as land.sh records a landed tip."""
    for name, text in files.items():
        write(repo, name, text)
    commit_all(repo, subject)
    sha = git(repo, "rev-parse", "HEAD").strip()
    with (repo / ".git" / "goh-landed").open("a") as f:
        f.write(f"{sha} full-identity\n")
    return sha


def test_a_diffcov_red_names_the_land_of_each_uncovered_line_and_never_bisects(
    shipped: Path, tmp_path: Path
) -> None:
    _declare_dummy(shipped)
    a = _land(shipped, {"src/a.rs": "fn a() {}\n"}, "feat: a, uncovered")
    b = _land(shipped, {"src/b.rs": "fn b() {\n    x();\n}\n"}, "feat: b, line 2 uncovered")
    tip = _land(shipped, {"cov.uncovered": "    src/b.rs:2      x();\n    src/a.rs:1  fn a() {}\n"},
                "fix: the tip")  # fmt: skip
    code, out, res = _ship(shipped, tmp_path)
    assert code == 1 and res["failed_step"] == "suite", out
    assert _judged(tmp_path) == [tip], "a diff coverage red ran a bisect probe"
    assert "bisect" not in res and res["status"] == "failed" and res["finished"], res
    lines = {(x["file"], x["line"]): x for x in res["diffcov_red"]["lines"]}
    assert lines[("src/b.rs", 2)]["land"] == b, lines
    assert lines[("src/b.rs", 2)]["subject"] == "feat: b, line 2 uncovered", lines
    assert lines[("src/a.rs", 1)]["land"] == a, lines
    assert any("src/b.rs:2" in t and b[:12] in t for t in res["tail"]), res["tail"]
    filed = [json.loads(c[c.index("--json") + 1]) for c in _calls(tmp_path) if "file" in c]
    assert sorted(f["id"] for f in filed) == sorted(
        f"GOH.SHIPRED{s[:8].upper()}" for s in (a, b)
    ), filed


def test_a_failing_test_red_bisects_on_that_test_alone_saying_bisecting(
    shipped: Path, tmp_path: Path
) -> None:
    _declare_dummy(shipped)
    first = _land(shipped, {"land0.txt": "0\n"}, "fix: land 0")
    culprit = _land(shipped, {"cov.fail": "1\n"}, "fix: the land that breaks test_mark")
    tip = _land(shipped, {"land2.txt": "2\n"}, "fix: land 2")
    code, out, res = _ship(shipped, tmp_path)
    assert code == 1 and res["failed_step"] == "suite", out
    assert _judged(tmp_path) == [tip], "a bisect step ran the full instrumented suite"
    assert res["bisect"]["culprit"] == culprit, res["bisect"]
    assert res["bisect"]["tests"] == ["tests/t_mark.py::test_mark"], res["bisect"]
    assert res["status"] == "failed" and res["finished"] and "bisect_step" not in res, res
    seen = (tmp_path / "probe-records").glob("*.json")
    probes = {p.stem: json.loads(p.read_text()) for p in seen}
    assert set(probes) == {first, culprit}, probes  # halving: the first land green, then red
    for sha, mid in probes.items():  # the record while the worker bisects
        assert mid["status"] == "bisecting" and "finished" not in mid, mid
        assert mid["bisect_step"]["sha"] == sha and mid["bisect_step"]["lands"] == 3, mid


def test_a_second_ship_finding_the_lock_held_names_the_holder_and_its_state(
    shipped: Path, tmp_path: Path
) -> None:
    import subprocess

    from test_ship_starve import _env_for

    holder = tmp_path / "20261122T021940Z-84cbc55f.json"
    probe = {"n": 2, "lands": 3, "sha": "da695d93" * 5}
    alive = os.getpid()
    gdb.ship(holder, {"status": "bisecting", "pid": alive, "bisect_step": probe})
    ship_status.settle_all()  # every ship the suite starts does this, over every record in goh.db
    with (tmp_path / ".ship.lock").open("a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held.write(json.dumps({"result": str(holder), "pid": alive}))
        held.flush()
        argv = [sys.executable, str(REPO_ROOT / "tools" / "ship.py"), "--repo", str(shipped),
                "--out", str(tmp_path / "ship.json"), "--wait"]  # fmt: skip
        r = subprocess.run(argv, env=_env_for(tmp_path), capture_output=True, text=True,
                           timeout=patience(120), stdin=subprocess.DEVNULL,
                           check=False)  # fmt: skip
    said = r.stdout + r.stderr
    assert r.returncode == 2, said
    assert str(holder) in said and f"pid {alive}" in said, said
    assert "bisecting" in said and "bisect step 2" in said and "da695d93" in said, said
    assert gdb.shipped(tmp_path / "ship.json") is None, "a refused ship left a record"
