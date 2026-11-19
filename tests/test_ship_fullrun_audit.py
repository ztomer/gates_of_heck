"""The ship's suite step stands on a land's full run by an exact key, sampled (GOH.ONEVERDICTTREE4).

v0.139.14's ship skipped its suite step, "ef531cbc already has its instrumented full run (by land
gate)": the land gate's suite had judged every test of that very tree itself, against the
instrumented binary, and recorded it (lib/reuse.py `carry`, `full_instrumented`). That skip is a
reuse on (repo, tree, suite tools); the suite's other inputs cannot differ between the two runs
(tests/suite_home.py drops every GOH_* but its CALLER_INPUTS before a test runs, and every run
that writes the record runs in the land's `ci` shape: a land key forces it). But it asked no audit:
no skip of the suite step was ever re-run to see the verdict hold. It is the audited `fullrun`
path now: one skip in 10 runs the suite anyway, fresh (no same-tree reuse), and compares; a
repeated disagreement turns the path off, and an off path runs every ship's suite.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, commit_all, git, write

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_identity
import reuse
import reuse_audit
import ship_full_run


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl"))
    for k in ("GOH_REUSE_TTL_S", "GOH_REUSE_NOW"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path / "store"


@pytest.fixture
def full(repo: Path) -> Path:
    write(repo, ".gatesrc", "GOH_SHIP_FULL_SUITE=on\n")
    commit_all(repo, "chore: a repo whose ship stands on a full run")
    return repo


def _land(repo: Path, *, full: bool = True, instrumented: bool = True, tools: str = "") -> None:
    """The land gate's suite on HEAD's tree, as tests/_land_verdicts.py records it."""
    tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
    reuse.record_suite(repo, tree, tools or gate_identity.suite_tools(), {"t.py::a": True}, set(),
                       full, "land gate", carried=0 if full else 3,
                       instrumented=instrumented)  # fmt: skip


def _head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def test_a_land_gates_full_instrumented_run_of_the_tree_is_the_ships_suite_step(store, full):
    _land(full)
    kind, why = ship_full_run.decide(full, _head(full))
    assert kind == "skip" and "already has its instrumented full run (by land gate)" in why, why
    assert reuse_audit.state("fullrun")["window"]["uses"] == 1  # every skip is one audited use


@pytest.mark.parametrize("moved", ["tree", "tools", "carried", "plain", "red"])
def test_each_input_that_moved_owes_the_ship_its_own_run(store, full, moved) -> None:
    _land(full, full=moved != "carried", instrumented=moved != "plain",
          tools="OTHER-TOOLS" if moved == "tools" else "")  # fmt: skip
    if moved == "tree":  # a land after the judged one: main's tree is another
        write(full, "next.txt", "1\n")
        commit_all(full, "fix: the next land")
    if moved == "red":  # a later run of that tree failed a test
        tree = git(full, "rev-parse", "HEAD^{tree}").strip()
        reuse.record_suite(full, tree, gate_identity.suite_tools(), {}, {"t.py::a"}, False, "gate")
    assert ship_full_run.decide(full, _head(full)) == ("run", "")


def test_one_skip_in_ten_is_a_fresh_run_compared_and_a_repeated_red_turns_the_path_off(
    store, full, tmp_path: Path
) -> None:
    _land(full)
    kinds = [ship_full_run.decide(full, _head(full))[0] for _ in range(10)]
    assert kinds == ["skip"] * 9 + ["shadow"], kinds
    log = tmp_path / "env.log"
    code = (
        "import os, sys; open(sys.argv[1], 'a').write("
        "os.environ.get('GOH_LAND_VERDICTS_REUSE', '-') + '\\n'); sys.exit(1)"
    )
    probe = [sys.executable, "-c", code, str(log)]
    rc = ship_full_run.run(full, tmp_path / "wt", probe, shadow=True)
    assert rc == 1  # the fresh run's own verdict decides the step
    assert log.read_text().split() == ["-", "-"], log.read_text()  # fresh, and re-run on a red
    st = reuse_audit.state("fullrun")
    assert st["state"] == "off" and st["cause"] == "disagree", st
    assert ship_full_run.decide(full, _head(full))[0] == "run"  # an off path skips no ship's run


def test_a_sampled_skip_that_agrees_keeps_the_path_on(store, full, tmp_path: Path) -> None:
    _land(full)
    rc = ship_full_run.run(full, tmp_path / "wt", [sys.executable, "-c", "pass"], shadow=True)
    assert rc == 0
    st = reuse_audit.state("fullrun")
    assert st["state"] == "on" and st["window"]["agreed"] == 1, st


def test_the_ship_runs_a_sampled_skip_as_its_step(store, full, monkeypatch) -> None:
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import ship
    finally:
        del sys.path[0]
    _land(full)
    res = {"repo": str(full), "result": str(full.parent / "ship.json"), "loopctl": "loopctl"}
    monkeypatch.setattr(ship_full_run, "decide", lambda repo, sha: ("shadow", "sampled"))
    cmd, _ = ship.command(res, "suite")
    assert cmd is not None and cmd.endswith(" --shadow"), cmd
    monkeypatch.setattr(ship_full_run, "decide", lambda repo, sha: ("skip", "held"))
    assert ship.command(res, "suite") == (None, "held")


def test_the_cli_takes_the_shadow_flag(store, full, tmp_path: Path) -> None:
    argv = [sys.executable, str(REPO_ROOT / "lib" / "ship_full_run.py"), "--repo", str(full),
            "--wt", str(tmp_path / "wt"), "--help"]  # fmt: skip
    out = subprocess.run(argv, capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL)
    assert "--shadow" in out.stdout
    assert json.dumps(reuse.PATHS["fullrun"].fields) == json.dumps(["repo", "tree", "tools"])


@pytest.mark.parametrize("land", ["full", "carried"])
def test_the_idle_suite_skips_a_tip_a_land_gate_judged_whole_and_runs_one_it_carried(
    store, full, tmp_path: Path, monkeypatch, land
) -> None:
    """D3: the idle run exists to make main's full record; a land gate that judged every test of
    the tip's tree itself, instrumented, made it, so the idle run owes nothing. A land that
    carried verdicts from another tree made none (the owner's term 2): the idle run is owed."""
    import idle_suite

    for k in (*idle_suite.IN_SUITE, idle_suite.OFF_ENV):
        monkeypatch.delenv(k, raising=False)
    _land(full, full=land == "full")
    q, ran = tmp_path / "landq", tmp_path / "ran"
    for d in ("pending", "logs"):
        (q / d).mkdir(parents=True)
    argv = [sys.executable, "-c", f"open({str(ran)!r}, 'w').close()"]
    want = "nothing" if land == "full" else "green"
    assert idle_suite.run(q, full, say=lambda _: None, argv=argv, poll=0.05) == want
    assert ran.exists() == (land == "carried")
