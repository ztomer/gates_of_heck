"""A gate run in a loop lane's worktree takes its slot as `lane` (GOH.LANECLASS).

GOH.SLOTPRIORITY ranks the long lane land > ship > lane > work, but nothing declared `lane`: a
lane's `tools/pytest.sh`, `land --check` and push gates all queued as `work`, behind the sweep's
consumer pushes. Asking every lane to export GOH_GATE_CLASS=lane is a rule nobody holds, so the
class is derived from where the gate runs: a checkout under the loop's lanes root
($LOOPCTL_LANES_ROOT, else $XDG_STATE_HOME/loop/lanes, else ~/.local/state/loop/lanes) is `lane`
unless its caller declared another class. Pinned here:

  * the resolution (lib/gate_lanes.py `default_class`, `lanes_root`): the three roots in order,
    a checkout under the root or the root itself, a sibling prefix that is not under it, a
    symlinked component, an explicit class winning (`work` included);
  * the shell's acquire (lib/gate_slot.sh) and the one-process admission (tools/load.py through
    lib/admission.py) both take it: in a lane worktree `lane`, outside `work`, declared wins;
  * the land profile shows the lane's wait as `slot wait lane`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import timing_bounds
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_lanes
import land_attempts
import land_profile

LOAD = REPO_ROOT / "tools" / "load.py"


def _lane_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """(lanes root, a worktree under it, a checkout outside it)."""
    root = tmp_path / "lanes"
    wt = root / "gates_of_heck" / "4" / "wt"
    wt.mkdir(parents=True)
    out = tmp_path / "proj"
    out.mkdir()
    return root, wt, out


def test_a_checkout_under_the_lanes_root_is_lane_and_one_outside_is_work(tmp_path: Path) -> None:
    root, wt, out = _lane_tree(tmp_path)
    env = {"LOOPCTL_LANES_ROOT": str(root)}
    assert gate_lanes.default_class(str(wt), env) == "lane"
    assert gate_lanes.default_class(str(wt / ".."), env) == "lane"  # still under the root
    assert gate_lanes.default_class(str(root), env) == "lane"
    assert gate_lanes.default_class(str(out), env) == "work"
    sibling = tmp_path / "lanes-old"  # shares the prefix, is not under the root
    sibling.mkdir()
    assert gate_lanes.default_class(str(sibling), env) == "work"


def test_an_explicit_class_wins_in_a_lane_worktree(tmp_path: Path) -> None:
    root, wt, _ = _lane_tree(tmp_path)
    for declared in ("work", "land", "ship", "short"):
        env = {"LOOPCTL_LANES_ROOT": str(root), "GOH_GATE_CLASS": declared}
        assert gate_lanes.default_class(str(wt), env) == declared
    assert (
        gate_lanes.default_class(str(wt), {"LOOPCTL_LANES_ROOT": str(root), "GOH_GATE_CLASS": " "})
        == "lane"
    )


def test_the_lanes_root_is_the_loops_own_spelling(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state = tmp_path / "state"
    assert gate_lanes.lanes_root(
        {"LOOPCTL_LANES_ROOT": str(tmp_path / "a"), "XDG_STATE_HOME": str(state)}
    ) == str((tmp_path / "a").resolve())
    assert gate_lanes.lanes_root({"XDG_STATE_HOME": str(state)}) == str(
        (state / "loop" / "lanes").resolve()
    )
    got = gate_lanes.lanes_root({"HOME": str(home)})
    assert got.endswith("/.local/state/loop/lanes"), got


def test_a_symlinked_component_does_not_hide_a_lane(tmp_path: Path) -> None:
    root, wt, _ = _lane_tree(tmp_path)
    link = tmp_path / "shortcut"
    link.symlink_to(wt)
    assert gate_lanes.default_class(str(link), {"LOOPCTL_LANES_ROOT": str(root)}) == "lane"


def _acquire_class(tmp_path: Path, cwd: Path, **extra: str) -> str:
    """The class the shell's acquire records (`GATE_SLOT_CLASS`) for a gate run in `cwd`."""
    script = (
        f'. "{REPO_ROOT}/tui/lib.sh"; . "{REPO_ROOT}/lib/gate_slot.sh"; '
        "gate_slot_acquire repo >/dev/null || exit $?; "
        'echo "CLASS=$GATE_SLOT_CLASS"; gate_slot_release'
    )
    env = hermetic_env(
        NO_COLOR="1", GOH_MEM_ADMIT="0", GOH_GATE_SLOTS="1",
        GOH_GATE_SLOT_DIR=str(tmp_path / "slots"), **extra,
    )  # fmt: skip
    got = subprocess.run(
        ["bash", "-c", script], env=env, cwd=cwd, capture_output=True, text=True,
        timeout=timing_bounds.patience(60), stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert got.returncode == 0, got.stdout + got.stderr
    return got.stdout.split("CLASS=")[1].strip()


def test_the_shell_acquire_takes_lane_in_a_lane_worktree_work_outside_and_the_declared(
    tmp_path: Path,
) -> None:
    root, wt, out = _lane_tree(tmp_path)
    where = {"LOOPCTL_LANES_ROOT": str(root)}
    assert _acquire_class(tmp_path, wt, **where) == "lane"
    assert _acquire_class(tmp_path, out, **where) == "work"
    assert _acquire_class(tmp_path, wt, GOH_GATE_CLASS="land", **where) == "land"
    assert _acquire_class(tmp_path, wt, GOH_GATE_CLASS="work", **where) == "work"


@pytest.mark.load_sensitive
def test_a_load_admission_in_a_lane_worktree_says_lane(tmp_path: Path) -> None:
    """lib/admission.py (tools/load.py's one entry) derives it too, and says it."""
    root, wt, out = _lane_tree(tmp_path)

    def said(cwd: Path, **extra: str) -> str:
        env = hermetic_env(
            NO_COLOR="1", GOH_MEM_ADMIT="0", GOH_GATE_SLOTS="1",
            GOH_GATE_SLOT_DIR=str(tmp_path / "slots"), GOH_LOAD_DIR=str(tmp_path / "load"),
            LOOPCTL_LANES_ROOT=str(root), **extra,
        )  # fmt: skip
        got = subprocess.run(
            [sys.executable, str(LOAD), "--background-ok", "test", "--wait-max", "20s", "0",
             "--", "/usr/bin/true"],
            env=env, cwd=cwd, capture_output=True, text=True, timeout=timing_bounds.patience(60),
            stdin=subprocess.DEVNULL, check=False,
        )  # fmt: skip
        assert got.returncode == 0, got.stdout + got.stderr
        return got.stdout + got.stderr

    assert "class lane" in said(wt)
    assert "class lane" not in said(out)
    assert "class ship" in said(wt, GOH_GATE_CLASS="ship")


def test_the_land_profile_shows_a_lanes_wait(tmp_path: Path) -> None:
    q = tmp_path / "q"
    for d in ("done", "logs"):
        (q / d).mkdir(parents=True)
    rec = {"repo": "/p/gates_of_heck", "mode": "land", "state": "landed", "item": "GOH.X",
           "started": "2026-11-21T10:05:00+00:00", "finished": "2026-11-21T10:20:00+00:00",
           "log": str(q / "logs" / "1-GOH.X.log"), "slot_wait_s": 95.0,
           "slot_wait_by_class": {"land": 5.0, "lane": 90.0}}  # fmt: skip
    (q / "done" / "01795255200000000000-GOH.X.json").write_text(
        os.linesep.join([__import__("json").dumps(rec)])
    )
    (q / "logs" / "1-GOH.X.log").write_text("")
    rows = land_profile.lands(q, [])
    assert rows[0]["stages"]["      slot wait lane"][0] == 90.0
    order = [r["stage"] for r in land_profile.table(rows)]
    assert "      slot wait lane" in order
    assert land_attempts.row_of(rec, "1-GOH.X")["slot_wait_by_class"]["lane"] == 90.0


def test_a_whole_hand_run_suite_takes_lane_in_a_lane_worktree_and_work_outside(
    tmp_path: Path,
) -> None:
    """tools/pytest.sh cds to its own checkout: a lane's whole run is `lane`, another's `work`,
    GOH_GATE_CLASS wins. The interpreter is a stub that runs the real admission and no pytest."""
    stub = tmp_path / "python3"
    stub.write_text(
        f'#!/bin/bash\ncase "$1" in -S | -c) exec "{sys.executable}" "$@" ;; esac\nexit 0\n'
    )
    stub.chmod(0o755)

    def said(lanes_root: Path, **extra: str) -> str:
        env = hermetic_env(
            NO_COLOR="1", GOH_MEM_ADMIT="0", GOH_GATE_SLOTS="1", GOH_PY=str(stub),
            GOH_PY_OK=str(stub), GOH_GATE_SLOT_DIR=str(tmp_path / "slots"),
            GATES_OF_HECK_SUITE_LOCK=str(tmp_path / "s.lock"),
            LOOPCTL_LANES_ROOT=str(lanes_root), GOH_SUITE_CORES="2", **extra,
        )  # fmt: skip
        for k in ("GATES_OF_HECK_SUITE_RUNNER", "GATES_OF_HECK_SUITE_NESTED_OK", "GOH_GATE_SLOT"):
            env.pop(k, None)
        got = subprocess.run(
            ["bash", str(REPO_ROOT / "tools" / "pytest.sh")], env=env, capture_output=True,
            text=True, timeout=timing_bounds.patience(60), stdin=subprocess.DEVNULL, check=False,
        )  # fmt: skip
        assert got.returncode == 0, got.stdout + got.stderr
        return got.stdout + got.stderr

    assert "(GOH_GATE_SLOTS, class lane)" in said(REPO_ROOT.parent)
    assert "(GOH_GATE_SLOTS, class work)" in said(tmp_path / "elsewhere")
    assert "(GOH_GATE_SLOTS, class land)" in said(REPO_ROOT.parent, GOH_GATE_CLASS="land")
