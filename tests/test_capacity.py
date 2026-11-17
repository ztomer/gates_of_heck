"""`goh capacity --json`: the ONE reading of the machine's room for more work (GOH.ADAPTLANES).

Owner 2026-11-17: "are the number of lanes across the sessions adaptive to the available
resources?" -- they were not: lane counts were hand-set constants, every session decided alone, and
gates then queued on two slots at a load of 40-130 on 16 cores. lib/capacity.py reads the gate
slots' OWN state (their flock'd files and waiting/ tickets, lib/gate_slot.py), the load, memory,
disk and the CPU weight of the gates in flight (the one timing record), and judges room by one
rule. These tests plant a slot dir (a slot held by this process's flock, a live ticket and a dead
one) and fake the load average through GOH_LOADAVG_READING.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env, run_goh
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import capacity
import gate_lanes

CORES = os.cpu_count() or 1


class Planted:
    """A slot dir with slot-0 held (this process's flock, its record naming `goh`), one live long
    ticket, one dead one; a timing ledger saying a goh gate burns 7 cores; plenty of memory."""

    def __init__(self, tmp: Path, *, waiting: bool = True, holder: str = "goh") -> None:
        self.dir = tmp / "slots"
        self.dir.mkdir()
        self.fd = os.open(self.dir / "slot-0", os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(self.fd, f"{os.getpid()}\t{holder}\t{time.time() - 60:.0f}\tkey".encode())
        (self.dir / "slot-1").touch()  # free: nobody holds its lock
        self.ticket = gate_lanes.Ticket(str(self.dir), "long", 1) if waiting else None
        dead = self.dir / "waiting" / "long-1-00000000000000000001-1"
        dead.parent.mkdir(exist_ok=True)
        dead.touch()  # nobody holds it: a dead waiter's, never counted
        timing = tmp / "timing"
        timing.mkdir()
        rows = [{"v": 1, "subject": "goh land", "repo": "goh", "kind": "land", "wall_s": 1000.0,
                 "cpu_s": c} for c in (6000.0, 7000.0, 8000.0)]  # fmt: skip
        rows.append({"v": 1, "subject": "x push", "repo": "x", "kind": "push", "wall_s": 100.0,
                     "cpu_s": 100.0})  # fmt: skip
        (timing / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        mem = tmp / "mem"
        mem.write_text("avail_pct=80 swap_used_mb=0 mem_total_mb=65536")
        self.env = {
            "GOH_GATE_SLOTS": "2", "GOH_GATE_SLOT_DIR": str(self.dir),
            "GOH_LOADAVG_READING": f"{CORES * 4} {CORES * 3} {CORES * 2}",
            "GOH_TIMING_DIR": str(timing), "GOH_MEM_READING": str(mem),
            "GOH_CAPACITY_DISK": str(tmp),
        }  # fmt: skip

    def close(self) -> None:
        if self.ticket is not None:
            self.ticket.leave()
        os.close(self.fd)


def _cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib" / "capacity.py"), *args],
        capture_output=True, text=True, env=hermetic_env(**env), timeout=patience(60),
        check=False, stdin=subprocess.DEVNULL,
    )  # fmt: skip


def test_the_reading_names_slots_tickets_load_memory_disk_and_the_cpu_weight(tmp_path) -> None:
    p = Planted(tmp_path)
    try:
        r = _cli(p.env, "--json")
    finally:
        p.close()
    assert r.returncode == 1, r.stderr  # no room: a waiting ticket and the load
    got = json.loads(r.stdout)
    assert got["unread"] == [], got["unread"]
    assert (got["slots"]["total"], got["slots"]["held"], got["slots"]["free"]) == (2, 1, 1)
    (holder,) = got["slots"]["holders"]
    assert (holder["slot"], holder["repo"], holder["pid"]) == ("slot-0", "goh", os.getpid())
    assert (holder["weight"], holder["weight_from"]) == (7.0, "goh")  # the median of its rows
    assert got["cpu_weight"]["in_flight"] == 7.0
    assert got["waiting"] == {"long": 1, "short": 0}  # the dead ticket is not a waiter
    assert got["load"]["avg"] == [CORES * 4, CORES * 3, CORES * 2]
    assert (got["load"]["cores"], got["load"]["per_core"]) == (CORES, 3.0)  # the 5-min average
    assert got["memory"]["avail_pct"] == 80 and got["disk"]["free_gib"] > 0
    why = " ".join(got["room"]["why"])
    assert got["room"]["ok"] is False
    assert "1 gate waiting for a slot" in why and "GOH_LANE_MAX_WAITING=0" in why, why
    assert "3.00 per core" in why and "GOH_LANE_LOAD_PER_CORE=2" in why, why


def test_an_idle_reading_has_room_and_an_unknown_holder_weighs_the_machine_median(tmp_path):
    p = Planted(tmp_path, waiting=False, holder="wt: cargo test")
    p.env["GOH_LOADAVG_READING"] = "0.5 0.5 0.5"
    try:
        r = _cli(p.env, "--json")
    finally:
        p.close()
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout)
    assert got["room"] == {"ok": True, "why": [], "settings": got["room"]["settings"]}
    (holder,) = got["slots"]["holders"]
    assert (holder["repo"], holder["weight"], holder["weight_from"]) == ("wt", 6.5, "all runs")


def test_a_holder_in_a_lane_worktree_is_weighed_as_its_repository(tmp_path) -> None:
    # GOH.CPUADMIT: a lane's gate runs in `<scratch>/wt`, so its slot record's label says `wt`
    # (tools/load.py labels by the working directory) and it was weighed by every repo's median
    # (1.27-1.63 on 2026-11-17) instead of goh's (5.21). The record's repository key is the git
    # common dir the worktree shares with its repository: found among the registered repos, it
    # names the repo the timing record knows.
    # The layout git writes for a linked worktree, planted (no git spawned): the worktree's .git
    # file points at <main>/.git/worktrees/wt, whose `commondir` leads back to <main>/.git.
    main = tmp_path / "gates_of_heck"
    admin = main / ".git" / "worktrees" / "wt"
    admin.mkdir(parents=True)
    (admin / "commondir").write_text("../..\n")
    wt = tmp_path / "lane" / "wt"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {admin}\n")
    import lock_order

    key = lock_order.repo_key(wt)
    assert key and key == lock_order.repo_key(main)
    state = tmp_path / "estate" / "estate_audit.json"
    state.parent.mkdir()
    (state.parent / "installed_repos").write_text(f"{main}\n")
    p = Planted(tmp_path, waiting=False, holder="wt: cargo test")
    os.ftruncate(p.fd, 0)
    os.pwrite(p.fd, f"{os.getpid()}\twt: cargo test\t1794900000\t{key}".encode(), 0)
    p.env["GOH_ESTATE_STATE"] = str(state)
    try:
        r = _cli(p.env, "--json")
    finally:
        p.close()
    (holder,) = json.loads(r.stdout)["slots"]["holders"]
    assert (holder["repo"], holder["weight"], holder["weight_from"]) == ("goh", 7.0, "goh")


def test_a_reading_that_fails_is_named_unread_and_never_judged_no_room(tmp_path) -> None:
    p = Planted(tmp_path)
    p.env["GOH_LOADAVG_READING"] = "not a load"
    try:
        r = _cli(p.env, "--json")
    finally:
        p.close()
    assert r.returncode == 0, r.stdout + r.stderr  # unread admits
    got = json.loads(r.stdout)
    assert any("GOH_LOADAVG_READING" in u for u in got["unread"]), got["unread"]
    assert got["room"]["ok"] is True and got["room"]["unread"] is True


def test_a_disk_under_the_floor_is_no_room(tmp_path) -> None:
    p = Planted(tmp_path, waiting=False)
    p.env.update(GOH_LOADAVG_READING="0 0 0", GOH_LANE_MIN_FREE_GIB="100000")
    try:
        r = _cli(p.env, "--json")
    finally:
        p.close()
    why = " ".join(json.loads(r.stdout)["room"]["why"])
    assert r.returncode == 1 and "GOH_LANE_MIN_FREE_GIB=100000" in why, why


def test_the_human_form_says_the_verdict(tmp_path) -> None:
    p = Planted(tmp_path)
    try:
        r = _cli(p.env)
    finally:
        p.close()
    assert r.returncode == 1 and "no room for another write lane" in r.stdout + r.stderr, r.stdout


def test_goh_capacity_is_the_same_entry(tmp_path) -> None:
    p = Planted(tmp_path)
    try:
        r = run_goh(tmp_path, "capacity", "--json", env=hermetic_env(**p.env))
    finally:
        p.close()
    got = json.loads(r.stdout)
    assert r.returncode == 1 and got["slots"]["held"] == 1 and got["waiting"]["long"] == 1, r


def test_the_weight_is_one_function_cpuadmit_can_call() -> None:
    rows = [{"repo": "a", "kind": "push", "wall_s": 10.0, "cpu_s": c} for c in (10.0, 30.0)]
    assert capacity.gate_weight("a", rows) == (2.0, "a")
    assert capacity.gate_weight("b", rows) == (2.0, "all runs")
    assert capacity.gate_weight("a", []) == (None, "no runs")


def test_an_empty_load_seam_reads_the_os(tmp_path) -> None:
    p = Planted(tmp_path, waiting=False)
    p.env["GOH_LOADAVG_READING"] = ""
    try:
        got = json.loads(_cli(p.env, "--json").stdout)
    finally:
        p.close()
    assert got["unread"] == [] and len(got["load"]["avg"]) == 3, got
