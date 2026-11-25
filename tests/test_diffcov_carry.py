"""A land's uncovered changed lines are carried to the next ship (lib/diffcov_carry.py,
GOH.DIFFCOVCARRY).

Modelled on v0.143.0 (ship record 20261122T021940Z-84cbc55f): a land added a Rust file
(crates/goh/src/toolshell/grep_probe.rs) whose lines no test ran; the land only warned, and the
ship's diff coverage refused them after a 961 s suite. Here a fixture land leaves ONE uncovered
line: the land records it (file, line, text) and files its fix item at the landing, and the ship's
preflight refuses naming it in seconds -- until it is covered (the item's land) or deleted, or the
ship is told --allow-diffcov REASON.

Unmeasured is not uncovered (GOH.DIFFCOVUNMEASURED): the land of GOH.SELFWALKFILTER (0cfcb5a1)
added 428 Rust lines in files the last ship's profile never saw; no land can measure Rust, so they
were carried as uncovered, filed GOH.DIFFCOV0CFCB5A1 and refused the next ship until
--allow-diffcov. Here the carried line is MEASURED uncovered (its file is in the profile, no part
ran it); a line of a file new since the profile is carried `measured: false`, files nothing and
refuses no ship: the ship's own diff coverage judges it after the suite.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import _goh_db_fixture as gdb
import landq_state
import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))
import land_records

from lib import cov_risk, diffcov_carry

A = "crates/goh/src/a.rs"
PROBE = "crates/goh/src/probe.rs"
LINE = "return None;"
FAKE_LOOPCTL = """\
import json, os, sys
with open(os.environ["FAKE_LOOPCTL_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                           "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                          check=True, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip


def _commit(repo: Path, msg: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


def _probe(code: str) -> str:
    return f"//! the probe\nuse std::env;\n\n{code}\n#[inline]\nmod util;\n"


def _land(tmp_path: Path, monkeypatch, measured: bool = True) -> tuple[Path, Path]:
    """(main, the lane worktree): main shipped v0.1.0 with a kept profile; the lane changes
    probe.rs's line 4 to code no test ran, among lines that are no code. MEASURED: probe.rs was in
    the profile (line 4 executable, no part ran it); else the lane adds it, new since then."""
    main = tmp_path / "gates_of_heck"
    fast_init(main, "main")
    (main / A).parent.mkdir(parents=True)
    (main / A).write_text("".join(f"let a{i} = {i};\n" for i in range(1, 11)))
    if measured:
        (main / PROBE).write_text(_probe("let p = 0;"))
    _commit(main, "base")
    _git(main, "tag", "v0.1.0")
    parts = tmp_path / "parts"
    parts.mkdir()
    das = "".join(f"DA:{i},1\n" for i in range(1, 11))
    probe = f"SF:{main / PROBE}\nDA:4,0\nend_of_record\n" if measured else ""
    (parts / "part-workspace.info").write_text(f"SF:{main / A}\n{das}end_of_record\n{probe}")
    monkeypatch.setenv("GOH_SHIP_DIR", str(tmp_path / "ship"))
    assert cov_risk.keep(parts, main, cov_risk.summary_path(main))
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "lane", str(wt))
    (wt / PROBE).write_text(_probe(LINE))
    _commit(wt, "the land")
    return main, wt


def diffcov_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def _landed(q: Path, main: Path, wt: Path, rec: dict, item: str, filed: str = "") -> dict:
    """The landing record the lander writes: WT's tip fast-forwarded onto main."""
    sha = _git(wt, "rev-parse", "HEAD")
    _git(main, "merge", "-q", "--ff-only", sha)
    out = {"sha": sha, "enqueued": sha, "landed": sha, "repo": str(main), "mode": "land",
           "state": "landed", "item": item, "cov_risk": rec, "at": diffcov_now(),
           **({"diffcov_item": {"id": filed, "said": "filed"}} if filed else {})}  # fmt: skip
    gdb.land(q, f"{len(land_records.runs(q)):04d}-{item}", out)
    return out


def test_a_land_records_its_uncovered_line_and_files_its_item(tmp_path, monkeypatch):
    main, wt = _land(tmp_path, monkeypatch)
    lines, rec = cov_risk.report(wt, main)
    want = {"file": PROBE, "line": 4, "text": LINE, "own": True,
            "why": "external suite only, or no test", "measured": True}  # fmt: skip
    assert rec["carry"] == [want], rec
    assert rec["base"] == "v0.1.0" and rec["head"] == _git(wt, "rev-parse", "HEAD")
    assert any("diff coverage carry: 1 line" in ln for ln in lines), lines
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    monkeypatch.setenv("FAKE_LOOPCTL_LOG", str(tmp_path / "calls"))
    outcome = {"state": "landed", "sha": "abcdef0123456789", "repo": str(main)}
    got = diffcov_carry.file_item(outcome, {"cov_risk": rec}, str(fake), "GOH.LANE")
    assert got == {"diffcov_item": {"id": "GOH.DIFFCOVABCDEF01", "said": "filed"}}
    argv = json.loads((tmp_path / "calls").read_text().splitlines()[0])
    item = json.loads(argv[argv.index("file") + 2])
    assert item["id"] == "GOH.DIFFCOVABCDEF01" and item["status"] == "todo"
    assert item["phase"] == "SPD" and item["regression"] is True
    assert item["class_of"] == "GOH.DIFFCOVCARRY" and f"{PROBE}:4 `{LINE}`" in item["item"]
    # a red land, or a land with no line of its own, files nothing
    assert diffcov_carry.file_item({**outcome, "state": "red"}, {"cov_risk": rec}, str(fake)) == {}
    other = {"cov_risk": {"carry": [{**rec["carry"][0], "own": False}]}}
    assert diffcov_carry.file_item(outcome, other, str(fake)) == {}


def _measured(tmp_path: Path, main: Path, line: int, hits: int | None) -> None:
    """A later complete run of MAIN's tip kept as the newest profile: probe.rs's LINE hit HITS
    times (None: no executable line)."""
    parts = tmp_path / f"parts-{line}-{hits}"
    parts.mkdir()
    da = "" if hits is None else f"DA:{line},{hits}\n"
    (parts / "part-external.info").write_text(f"SF:{main / PROBE}\nDA:1,1\n{da}end_of_record\n")
    assert cov_risk.keep(parts, main, cov_risk.summary_path(main))


def test_the_ship_preflight_refuses_naming_the_line_until_it_is_measured(tmp_path, monkeypatch):
    main, wt = _land(tmp_path, monkeypatch)
    q = tmp_path / "landq"
    _lines, rec = cov_risk.report(wt, main)
    _landed(q, main, wt, rec, "GOH.LANE", filed="GOH.DIFFCOVLANE")
    head = _git(main, "rev-parse", "HEAD")
    why = diffcov_carry.refusal(main, "v0.1.0", head, q)
    assert why and f"{PROBE}:4  {LINE}" in why and "--allow-diffcov" in why, why
    # an unrelated later land judged with the same profile still carries it: still refused
    (wt / A).write_text((wt / A).read_text() + "let a11 = 11;\n")
    _commit(wt, "unrelated")
    _l, rec2 = cov_risk.report(wt, main)
    _landed(q, main, wt, rec2, "GOH.OTHER")
    head = _git(main, "rev-parse", "HEAD")
    assert diffcov_carry.refusal(main, "v0.1.0", head, q), "an unrelated land covers nothing"
    # the fix lane lands under the filed item: it ran plain, so nothing measured the line
    (wt / "crates/goh/src/probe_tests.rs").write_text("fn covers() {}\n")
    _commit(wt, "cover it")
    _landed(q, main, wt, {"lines": 0}, "GOH.DIFFCOVLANE")
    head = _git(main, "rev-parse", "HEAD")
    why = diffcov_carry.refusal(main, "v0.1.0", head, q)
    assert why and "item GOH.DIFFCOVLANE, landed, not measured since)" in why, why
    _measured(tmp_path, main, 4, 1)  # the idle lander's run of that tip: covered
    assert diffcov_carry.refusal(main, "v0.1.0", head, q) is None


def test_a_later_judgment_clears_no_carried_line_and_a_deletion_does(tmp_path, monkeypatch):
    main, wt = _land(tmp_path, monkeypatch)
    q = tmp_path / "landq"
    _lines, rec = cov_risk.report(wt, main)
    _landed(q, main, wt, rec, "GOH.LANE")  # filing failed: no item to clear it
    # a parallel lane, judged on a tip without the land's change, clears nothing
    par = tmp_path / "par"
    _git(main, "worktree", "add", "-q", "-b", "par", str(par), "v0.1.0")
    (par / A).write_text((par / A).read_text() + "let a12 = 12;\n")
    tip = _commit(par, "parallel")
    _git(main, "merge", "-q", "--no-ff", "-m", "land par", tip)
    blind = {"lines": 0, "head": tip, "base": "v0.1.0", "carry": []}
    _landed(q, main, main, blind, "GOH.PAR")
    assert diffcov_carry.refusal(main, "v0.1.0", _git(main, "rev-parse", "HEAD"), q)
    # nor does a later land's, of a tip holding the land: it read the same old profile
    _git(wt, "merge", "-q", "--ff-only", "main")
    (wt / A).write_text((wt / A).read_text() + "let a11 = 11;\n")
    _commit(wt, "later")
    judged = {"lines": 0, "head": _git(wt, "rev-parse", "HEAD"), "base": "v0.1.0", "carry": []}
    _landed(q, main, wt, judged, "GOH.LATER")
    head = _git(main, "rev-parse", "HEAD")
    assert diffcov_carry.refusal(main, "v0.1.0", head, q)
    _git(main, "tag", "v0.2.0")  # a land before the base tag was the last ship's
    assert diffcov_carry.refusal(main, "v0.2.0", head, q) is None
    text = (main / PROBE).read_text().replace(f"{LINE}\n", "")
    (main / PROBE).write_text(text)
    assert diffcov_carry.refusal(main, "v0.1.0", _commit(main, "delete it"), q) is None


def test_a_line_the_allowlist_names_is_neither_carried_nor_refused(tmp_path, monkeypatch):
    """The ship's diff coverage (gates/diff_cov.py) passes a changed line its diffcov_allow.json
    names with a reason, so the land warns of and carries none, and the ship's preflight refuses
    none: on 2026-11-24 it named crates/goh/src/privnames_add.rs:29, allowlisted since its land,
    among the 3 lines it would refuse (GOH.SHIPCOVRISK)."""
    main, wt = _land(tmp_path, monkeypatch)
    q = tmp_path / "landq"
    _lines, rec = cov_risk.report(wt, main)
    _landed(q, main, wt, rec, "GOH.LANE")  # carried before the entry was written
    assert diffcov_carry.refusal(main, "v0.1.0", _git(main, "rev-parse", "HEAD"), q)
    allow = {"entries": [{"file": PROBE, "text": LINE, "reason": "unreachable, and why"}]}
    (main / "diffcov_allow.json").write_text(json.dumps(allow))
    head = _commit(main, "allow it")
    assert diffcov_carry.refusal(main, "v0.1.0", head, q) is None
    _git(wt, "merge", "-q", "--ff-only", "main")
    lines, rec = cov_risk.report(wt, main)
    assert rec["carry"] == [] and PROBE not in rec["files"], rec
    assert not any(PROBE in ln for ln in lines), lines


def test_an_unmeasured_carry_files_no_item_and_refuses_no_ship(tmp_path, monkeypatch):
    main, wt = _land(tmp_path, monkeypatch, measured=False)
    lines, rec = cov_risk.report(wt, main)
    want = {"file": PROBE, "line": 4, "text": LINE, "own": True,
            "why": "new since the last ship's profile", "measured": False}  # fmt: skip
    assert rec["carry"] == [want], rec
    said = "\n".join(lines)
    assert "unmeasured" in said and "refuses before its suite" not in said, said
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    monkeypatch.setenv("FAKE_LOOPCTL_LOG", str(tmp_path / "calls"))
    outcome = {"state": "landed", "sha": "abcdef0123456789", "repo": str(main)}
    assert diffcov_carry.file_item(outcome, {"cov_risk": rec}, str(fake), "GOH.LANE") == {}
    assert not (tmp_path / "calls").exists(), "an unmeasured-only carry files nothing"
    # a mixed carry files its measured lines only
    hit = {"file": A, "line": 2, "text": "let a2 = 2;", "own": True, "why": "x", "measured": True}
    mixed = {"cov_risk": {"carry": [*rec["carry"], hit]}}
    assert diffcov_carry.file_item(outcome, mixed, str(fake), "GOH.LANE")["diffcov_item"]
    argv = json.loads((tmp_path / "calls").read_text().splitlines()[0])
    item = json.loads(argv[argv.index("file") + 2])
    assert f"{A}:2" in item["item"] and PROBE not in item["item"], item["item"]
    # the ship's preflight leaves it to the suite's diff coverage: the new record, and one kept
    # before GOH.DIFFCOVUNMEASURED (no `measured`, read from its `why`, as 0cfcb5a1's was)
    q = tmp_path / "landq"
    _landed(q, main, wt, rec, "GOH.LANE")
    head = _git(main, "rev-parse", "HEAD")
    assert diffcov_carry.refusal(main, "v0.1.0", head, q) is None
    old = {**rec, "carry": [{k: v for k, v in c.items() if k != "measured"} for c in rec["carry"]]}
    ((run, was),) = land_records.records(q)
    gdb.land(q, run, {**was, "cov_risk": old})
    assert diffcov_carry.refusal(main, "v0.1.0", head, q) is None


@pytest.mark.load_sensitive  # the preflight's wall time: seconds, against the suite's 961 s
def test_goh_ship_refuses_in_preflight_unless_allowed(tmp_path, tmp_path_factory):
    from _shipped_kit import shipped_clone
    from test_ship import _result, _ship
    from timing_bounds import assert_sooner

    repo = shipped_clone(tmp_path, tmp_path_factory)
    head = _git(repo, "rev-parse", "HEAD")
    carried = {"file": "f.txt", "line": 1, "text": "change", "own": True, "why": "new"}
    rec = {
        "sha": head,
        "landed": head,
        "repo": str(repo),
        "mode": "land",
        "state": "landed",
        "item": "GOH.LANE",
        "cov_risk": {"head": head, "base": "v1.0.0", "carry": [carried]},
    }
    q = tmp_path / "landq"
    gdb.land(q, "1-GOH.LANE", rec)
    t0 = time.monotonic()
    r = _ship(repo, tmp_path, "--wait", GOH_LANDQ_DIR=str(q))
    assert_sooner(time.monotonic() - t0, 4, 961, "goh ship's diff coverage preflight")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "f.txt:1  change" in r.stderr and gdb.shipped(tmp_path / "ship.json") is None, r.stderr
    r = _ship(repo, tmp_path, "--wait", "--allow-diffcov", " ", GOH_LANDQ_DIR=str(q))
    assert r.returncode == 2 and "needs a REASON" in r.stderr, r.stderr
    r = _ship(repo, tmp_path, "--wait", "--allow-diffcov", "covered by hand", GOH_LANDQ_DIR=str(q))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _result(tmp_path)["allow_diffcov"] == "covered by hand"


def test_goh_ship_starts_on_an_unmeasured_only_carry(tmp_path, tmp_path_factory):
    from _shipped_kit import shipped_clone
    from test_ship import _ship

    repo = shipped_clone(tmp_path, tmp_path_factory)
    head = _git(repo, "rev-parse", "HEAD")
    new = {"file": "f.txt", "line": 1, "text": "change", "own": True,
           "why": "new since the last ship's profile", "measured": False}  # fmt: skip
    risk = {"head": head, "base": "v1.0.0", "carry": [new]}
    rec = {"sha": head, "landed": head, "repo": str(repo), "mode": "land", "state": "landed",
           "item": "GOH.LANE", "cov_risk": risk}  # fmt: skip
    q = tmp_path / "landq"
    gdb.land(q, "1-GOH.LANE", rec)
    r = _ship(repo, tmp_path, "--wait", GOH_LANDQ_DIR=str(q))
    assert r.returncode == 0, "an unmeasured-only carry starts the ship: " + r.stdout + r.stderr


REFUSING_LOOPCTL = """\
import json, os, sys
item = json.loads(sys.argv[-1])
with open(os.environ["FAKE_LOOPCTL_LOG"], "a") as f:
    f.write(json.dumps(item) + "\\n")
if item["class_of"] != "none":
    sys.exit(print("GOH.DIFFCOVCARRY is done, so its check missed this instance") or 1)
if "GOH.DIFFCOVOLD" not in item.get("not_dup_of", {}):
    sys.exit(print("probably repeats open item GOH.DIFFCOVOLD (text overlap 0.91)") or 1)
"""


def test_a_closed_class_and_an_open_carry_item_still_file(tmp_path, monkeypatch):
    fake = tmp_path / "loopctl.py"
    fake.write_text(REFUSING_LOOPCTL)
    monkeypatch.setenv("FAKE_LOOPCTL_LOG", str(tmp_path / "calls"))
    carried = {"carry": [{"file": PROBE, "line": 4, "text": LINE, "own": True, "why": "new"}]}
    outcome = {"state": "landed", "sha": "0123456789", "repo": str(tmp_path)}
    got = diffcov_carry.file_item(outcome, {"cov_risk": carried}, str(fake), "ZT.LANE")
    assert got == {"diffcov_item": {"id": "ZT.DIFFCOV01234567", "said": "filed"}}
    tries = [json.loads(x) for x in (tmp_path / "calls").read_text().splitlines()]
    assert len(tries) == 3 and tries[-1]["class_why"] and tries[-1]["not_dup_of"]


def test_the_lander_files_the_item_and_names_it_on_the_landing_record(tmp_path, monkeypatch):
    import importlib.util

    if "landq" in sys.modules:
        landq = sys.modules["landq"]
    else:
        spec = importlib.util.spec_from_file_location("landq", REPO_ROOT / "tools" / "landq.py")
        landq = importlib.util.module_from_spec(spec)
        sys.modules["landq"] = landq
        spec.loader.exec_module(landq)
    db = gdb.use(monkeypatch, tmp_path)
    q = tmp_path / "landq"
    (q / "logs").mkdir(parents=True)
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    monkeypatch.setenv("GOH_LOOPCTL", str(fake))
    monkeypatch.setenv("FAKE_LOOPCTL_LOG", str(tmp_path / "calls"))
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "timing"))
    carried = {"carry": [{"file": PROBE, "line": 4, "text": LINE, "own": True, "why": "new"}]}
    req = {"sha": "fedcba9876543210", "repo": str(tmp_path), "mode": "land", "item": "GOH.LANE",
           "worktree": str(tmp_path), "cov_risk": carried}  # fmt: skip
    path = landq_state.put(q, "01-GOH.LANE", req, "running")  # a goh.db row (GOH.LANDQDB)
    outcome = {"sha": req["sha"], "repo": str(tmp_path), "mode": "land", "state": "landed"}
    landq.record(path, outcome, "GOH.LANE")
    done = land_records.get(q, path.stem)
    with sqlite3.connect(db) as con:  # the record's row in goh.db (GOH.STATEDB)
        row = con.execute("SELECT run, item, state, diffcov_item FROM lands").fetchall()
    assert row == [("01-GOH.LANE", "GOH.LANE", "landed", "GOH.DIFFCOVFEDCBA98")], row
    assert done["diffcov_item"] == {"id": "GOH.DIFFCOVFEDCBA98", "said": "filed"}, done
    calls = [json.loads(x) for x in (tmp_path / "calls").read_text().splitlines()]
    assert any("file" in c for c in calls) and any("set" in c for c in calls), calls


@pytest.mark.parametrize("hits", [1, 0, None])
def test_a_carry_is_rejudged_against_the_newest_ship_profile(tmp_path, monkeypatch, hits):
    """GOH.CARRYREJUDGE: GOH.LANDRUSTCOV carried observed.rs `_ => Vec::new(),` judged on the
    v0.144.0 profile; v0.145.0's profile covered it, yet the preflight refused two ships. A carry
    judged on an older profile is re-judged on the newest: covered there, or no executable line,
    clears it, else refuses."""
    main, wt = _land(tmp_path, monkeypatch)
    q = tmp_path / "landq"
    _lines, rec = cov_risk.report(wt, main)
    _landed(q, main, wt, rec, "GOH.LANE")
    (main / PROBE).write_text("// moved down\n" + (main / PROBE).read_text())
    _commit(main, "shift the line to 5")
    _measured(tmp_path, main, 5, hits)  # the newer ship's profile
    (main / PROBE).write_text("// again\n" + (main / PROBE).read_text())
    head = _commit(main, "after that ship: the line is 6")
    why = diffcov_carry.refusal(main, "v0.1.0", head, q)
    if hits == 0:
        assert why and f"{PROBE}:6  {LINE}" in why, why
    else:
        assert why is None, why
