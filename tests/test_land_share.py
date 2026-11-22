"""The land path takes most of the cores and runs at default QoS; lane/work gates split the rest
under a CPU clamp (GOH.LANDSHARE, lib/land_share.py).

2026-11-21: every admitted gate got `cores // admitted`, so a land's pre-step and the lander's
pre-land ran at 5 of 16 workers beside lane gates -- 1314 s for one pre-step, then the same set
again at 5 workers in the lander. Pinned here: the weighted rule and THE BOUND; the live token
pool moving within one test as a land-path run starts and ends; both admissions (tools/load.py's
in-process one and the shell's) exporting the weighted share; the pre-step declaring the land
path's `pre` class; and the clamp -- macOS `taskpolicy -c utility`, Linux nice + ionice, behind
the GOH_QOS_OS seam -- applied to lane/work runs and never to the land path.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from pathlib import Path

import pytest
import timing_bounds
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "lib"))
import land_share
import worker_share

LOAD = REPO_ROOT / "tools" / "load.py"


# --- the rule -------------------------------------------------------------------------------------


def test_a_land_path_run_beside_two_lane_gates_holds_three_quarters_of_the_cores() -> None:
    classes = ["pre", "lane", "lane"]
    assert land_share.weighted(16, classes, "pre") == 12
    assert land_share.weighted(16, classes, "lane") == 2
    assert land_share.weighted(16, ["land", "work", "lane"], "land") == 12


def test_with_no_land_path_run_the_share_is_cores_over_admitted() -> None:
    assert land_share.weighted(16, ["lane", "lane", "work"], "lane") == 5
    assert (
        land_share.weighted(16, ["short", "lane"], "short") == 8
    )  # a commit gate is not the land path


def test_land_path_runs_split_their_part_and_one_alone_takes_the_machine() -> None:
    assert land_share.weighted(16, ["pre", "land", "lane"], "pre") == 6
    assert land_share.weighted(16, ["pre", "land", "lane"], "lane") == 4
    assert land_share.weighted(16, ["land"], "land") == 16
    assert land_share.weighted(16, ["pre", "lane"], "pre", frac=0.5) == 8


@pytest.mark.parametrize("frac", [0.25, 0.5, 0.75, 0.9, 1.0])
def test_the_bound_the_shares_sum_to_at_most_the_cores(frac: float) -> None:
    """THE BOUND (lib/worker_share.py): whenever the gates fit at one core each, the workers of
    every admitted gate sum to at most the cores, and none gets zero."""
    for cores in (3, 4, 16):
        for land in range(cores + 1):
            for rest in range(cores + 1 - land):
                classes = ["pre"] * land + ["lane"] * rest
                if not classes:
                    continue
                shares = [land_share.weighted(cores, classes, c, frac) for c in classes]
                assert min(shares) >= 1, (cores, land, rest, shares)
                assert sum(shares) <= cores, (cores, land, rest, frac, shares)


def test_the_knob_is_one_fraction_from_the_env_or_the_gatesrc() -> None:
    assert land_share.fraction({}) == 0.75
    assert land_share.fraction({"GOH_LAND_SHARE": "0.5"}) == 0.5
    assert land_share.fraction({}, gatesrc=lambda k: "0.6" if k == "GOH_LAND_SHARE" else "") == 0.6
    resolved = {"GOH_SHARE_LAND": "0.8", "GOH_LAND_SHARE": "0.5"}
    assert land_share.fraction(resolved) == 0.8  # the admission's resolution wins
    for bad in ("0", "1.5", "most", "-1"):
        with pytest.raises(ValueError, match="GOH_LAND_SHARE"):
            land_share.fraction({"GOH_LAND_SHARE": bad})


# --- the live pool --------------------------------------------------------------------------------


def _hold_as(tmp_path: Path, name: str, klass: str | None):
    """A held slot whose record names `klass` (lib/admission.py's five fields), or an old record."""
    (tmp_path / "slots").mkdir(exist_ok=True)
    f = open(tmp_path / "slots" / name, "w")  # noqa: SIM115  -- held until closed
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    tail = f"\t{klass}" if klass else ""
    f.write(f"999999\tprobe\t0\tkey{tail}\n")  # an owner outside this ancestry
    f.flush()
    return f


def _seams(tmp_path: Path, klass: str, own: str = "0") -> dict[str, str]:
    """What live() reads: the cores, the slots, whether this run holds one, and its class."""
    return {"GOH_GATE_CORES": "16", "GOH_GATE_SLOT_DIR": str(tmp_path / "slots"),
            "GOH_SHARE_OWN": own, "GOH_SHARE_CLASS": klass}  # fmt: skip


def _batch(pool: worker_share.Pool, workers: int = 16) -> int:
    fds = [pool.take(w, wait=False) for w in range(workers)]
    got = [fd for fd in fds if fd is not None]
    for fd in got:
        os.close(fd)
    return len(got)


def _pool(tmp_path: Path, env: dict[str, str], name: str) -> worker_share.Pool:
    return worker_share.Pool(
        str(tmp_path / name), 16, width=lambda: worker_share.live(env), period=0
    )


def test_a_land_path_runs_live_pool_holds_three_quarters_beside_two_lane_gates(tmp_path) -> None:
    held = [_hold_as(tmp_path, "short-0", "pre"), _hold_as(tmp_path, "slot-0", "lane"),
            _hold_as(tmp_path, "slot-1", "lane")]  # fmt: skip
    try:
        pre, lane = _seams(tmp_path, "pre"), _seams(tmp_path, "lane")
        assert _batch(_pool(tmp_path, pre, "pre")) == 12
        assert _batch(_pool(tmp_path, lane, "lane")) == 2
    finally:
        for f in held:
            f.close()


def test_a_lane_run_shrinks_within_one_test_while_a_land_path_run_is_admitted(tmp_path) -> None:
    me, other = _hold_as(tmp_path, "slot-0", "lane"), _hold_as(tmp_path, "slot-1", "lane")
    try:
        pool = _pool(tmp_path, _seams(tmp_path, "lane"), "lane")
        assert _batch(pool) == 8  # two lanes: cores // admitted
        pre = _hold_as(tmp_path, "short-0", "pre")
        try:
            assert _batch(pool) == 2  # the pre-step takes 12: the lanes split the other 4
        finally:
            pre.close()
        assert _batch(pool) == 8  # and get them back the test after it ends
    finally:
        me.close()
        other.close()


def test_a_land_path_run_outside_any_slot_counts_itself_as_land_path(tmp_path) -> None:
    held = [_hold_as(tmp_path, "slot-0", "lane"), _hold_as(tmp_path, "slot-1", "work")]
    try:
        pre, work = (
            worker_share.live(_seams(tmp_path, "pre", own="1")),
            worker_share.live(_seams(tmp_path, "work", own="1")),
        )
        assert (pre, work) == (12, 5)  # beside a lane and a work gate; no land run: 16 // 3
    finally:
        for f in held:
            f.close()


def test_a_slot_record_from_before_the_class_counts_by_its_lane(tmp_path) -> None:
    held = [_hold_as(tmp_path, "short-0", None), _hold_as(tmp_path, "slot-0", None)]
    try:
        names = sorted(land_share.held_classes(str(tmp_path / "slots")))
        assert names == ["short", "work"]
    finally:
        for f in held:
            f.close()


# --- the admissions -------------------------------------------------------------------------------


def _admit_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return hermetic_env(NO_COLOR="1", GOH_MEM_ADMIT="0", GOH_GATE_SLOTS="2",
                        GOH_GATE_SLOT_DIR=str(tmp_path / "slots"), **extra)  # fmt: skip


def test_the_load_admission_gives_a_pre_step_three_quarters_and_records_its_class(tmp_path) -> None:
    held = [_hold_as(tmp_path, "slot-0", "lane"), _hold_as(tmp_path, "slot-1", "lane")]
    code = (
        f"import sys; sys.path.insert(0, {str(REPO_ROOT / 'lib')!r}); import os, admission\n"
        "got = admission.admit_here('probe', max_wait=20)\n"
        "e = os.environ\n"
        "print(got.state, got.gate_class, e['GOH_GATE_WORKERS'], e['GOH_SHARE_CLASS'])\n"
        "print(open(os.environ['GOH_GATE_SLOT'].rsplit(' ', 1)[0]).read().split('\\t')[-1].strip())"
    )
    try:
        got = subprocess.run(
            [sys.executable, "-c", code], env=_admit_env(tmp_path, GOH_GATE_CLASS="pre"),
            capture_output=True, text=True, timeout=timing_bounds.patience(60),
            stdin=subprocess.DEVNULL, check=False,
        )  # fmt: skip
    finally:
        for f in held:
            f.close()
    assert got.returncode == 0, got.stdout + got.stderr
    assert got.stdout.split() == ["taken", "pre", "12", "pre", "pre"], got.stdout + got.stderr


def test_the_shell_admission_gives_a_lane_gate_the_rest_beside_a_land_gate(tmp_path) -> None:
    held = [_hold_as(tmp_path, "slot-0", "land")]
    script = (
        f'. "{REPO_ROOT}/tui/lib.sh"; . "{REPO_ROOT}/lib/gate_slot.sh"; '
        "gate_slot_acquire probe lane 20 || exit 9; "
        'echo "W=$GOH_GATE_WORKERS C=$GOH_SHARE_CLASS"; gate_slot_release'
    )
    try:
        got = subprocess.run(
            ["bash", "-c", script], env=_admit_env(tmp_path), cwd=tmp_path, capture_output=True,
            text=True, timeout=timing_bounds.patience(60), stdin=subprocess.DEVNULL, check=False,
        )  # fmt: skip
    finally:
        for f in held:
            f.close()
    assert got.returncode == 0, got.stdout + got.stderr
    assert "W=4 C=lane" in got.stdout, got.stdout + got.stderr  # 16 - 12 for the one lane gate


def test_the_land_pre_step_and_pre_land_declare_the_land_paths_pre_class() -> None:
    land = (REPO_ROOT / "tools" / "land.sh").read_text(encoding="utf-8")
    pre = [
        line for line in land.splitlines() if '"$GOH/tools/load.py"' in line and "pre-step" in line
    ]
    assert pre and all("GOH_GATE_CLASS=pre " in line for line in pre), pre
    import gate_lanes

    assert gate_lanes.lane_of("pre") == ("pre", 0)  # a lane of its own, two at most (GOH.PREWIDTH)
    assert {"pre", "land", "ship"} == land_share.LAND_PATH


# --- the clamp ------------------------------------------------------------------------------------


def _which(*present: str):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def test_lane_and_work_gates_are_clamped_on_macos_and_the_land_path_never() -> None:
    env = {"GOH_QOS_OS": "darwin"}
    for klass in ("lane", "work"):
        argv, _ = land_share.qos_prefix(klass, env, _which("taskpolicy"))
        assert argv == ["taskpolicy", "-c", "utility"], klass
    for klass in ("pre", "land", "ship", "short"):
        argv, why = land_share.qos_prefix(klass, env, _which("taskpolicy"))
        assert argv == [] and "default QoS" in why, klass


def test_the_linux_seam_lowers_nice_and_io_and_never_touches_the_land_path() -> None:
    env = {"GOH_QOS_OS": "linux"}
    argv, _ = land_share.qos_prefix("lane", env, _which("nice", "ionice"))
    assert argv == ["nice", "-n", "10", "ionice", "-c", "2", "-n", "7"]
    argv, why = land_share.qos_prefix("lane", env, _which("nice"))
    assert argv == ["nice", "-n", "10"] and "ionice not found" in why
    assert land_share.qos_prefix("land", env, _which("nice", "ionice"))[0] == []


def test_a_missing_tool_degrades_with_its_reason_and_an_unsupported_os_refuses() -> None:
    argv, why = land_share.qos_prefix("lane", {"GOH_QOS_OS": "darwin"}, _which())
    assert argv == [] and "taskpolicy not found" in why
    argv, why = land_share.qos_prefix("lane", {"GOH_QOS_OS": "darwin", "GOH_LANE_QOS": "off"})
    assert argv == [] and "GOH_LANE_QOS=off" in why
    with pytest.raises(SystemExit, match=r"unsupported OS 'freebsd'.*GOH_QOS_OS"):
        land_share.qos_prefix("lane", {"GOH_QOS_OS": "freebsd"}, _which("nice"))


def _fake_tools(tmp_path: Path) -> Path:
    """taskpolicy and nice that write their argv to <name>.argv and exec the rest (the real ones
    exec in place too: probed, the pid is the program's)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, skip in (("taskpolicy", 2), ("nice", 2), ("ionice", 4)):
        tool = bin_dir / name
        tool.write_text(
            f'#!/bin/bash\nprintf "%s " "$@" >>"{tmp_path}/{name}.argv"\nshift {skip}\nexec "$@"\n'
        )
        tool.chmod(0o755)
    return bin_dir


def _load_run(tmp_path: Path, qos_os: str, **extra: str) -> subprocess.CompletedProcess:
    bin_dir = _fake_tools(tmp_path)
    env = _admit_env(
        tmp_path, GOH_LOAD_DIR=str(tmp_path / "load"), GOH_QOS_OS=qos_os, GOH_LANE_QOS="on",
        PATH=f"{bin_dir}:{os.environ['PATH']}", **extra,
    )  # fmt: skip
    work = tmp_path / "work"
    work.mkdir()
    return subprocess.run(
        [sys.executable, str(LOAD), "--wait-max", "20s", "0", "--", "/usr/bin/true"],
        env=env, cwd=work, capture_output=True, text=True, timeout=timing_bounds.patience(60),
        stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip


CLAMPS = [("darwin", "taskpolicy", "-c utility"), ("linux", "nice", "-n 10")]


@pytest.mark.load_sensitive  # load.py waits for its admission (--wait-max)
@pytest.mark.parametrize(("qos_os", "tool", "flags"), CLAMPS)
def test_a_work_class_load_run_execs_under_the_clamp(tmp_path, qos_os, tool, flags) -> None:
    got = _load_run(tmp_path, qos_os)
    assert got.returncode == 0, got.stdout + got.stderr
    argv = (tmp_path / f"{tool}.argv").read_text()
    assert argv.startswith(flags) and "/usr/bin/true" in argv, argv
    assert "wider deadlines" in got.stdout + got.stderr  # a timing-sensitive test is told


@pytest.mark.load_sensitive  # load.py waits for its admission (--wait-max)
@pytest.mark.parametrize("qos_os", ["darwin", "linux"])
def test_a_land_path_load_run_never_execs_under_the_clamp(tmp_path, qos_os) -> None:
    got = _load_run(tmp_path, qos_os, GOH_GATE_CLASS="land")
    assert got.returncode == 0, got.stdout + got.stderr
    assert not list(tmp_path.glob("*.argv")), [p.read_text() for p in tmp_path.glob("*.argv")]
