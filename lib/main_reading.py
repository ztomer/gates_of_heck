"""lib/main_reading.py -- what main's own tests said at one sha, read on main (GOH.MAINREDWAIVE).

WHY. A test red on main fails every item that runs it: one test red 11 lands of 10 items on
2026-11-22/23, and in the 24 h before this change 85 lands went red for 45 landed. An item's red on
such a test is main's, not the item's -- but only a reading ON MAIN, at the item's own base, may say
so: never the test's name, never a flake guess. This module keeps those readings (goh.db 0010):

    record(repo, sha, source, ran, red)  one reading: SOURCE `idle-suite` ran every test (RAN '*'),
                                         `probe` ran RAN (node ids); RED the nodes it saw red;
                                         its id.
    red_at(repo, sha)                    {node: reading id} for each node red at SHA by the newest
                                         reading that ran it (a later green run of it clears it)
    covered(repo, sha, nodes)            whether some reading at SHA ran every one of NODES
    probe(q, repo, sha, nodes)           NODES run on a clean checkout of main at SHA (a worktree
                                         of its own under Q, removed after), recorded; its id, or
                                         None when the run reached no verdict (a timeout, a
                                         collection error naming no test)
    ensure(q, repo)                      before a land's gate: when the idle full suite last found
                                         main red (lib/main_red.py `red`) and no reading at main's
                                         tip ran those nodes, probe them there, so the gate of the
                                         FIRST item on this tip already knows main's own reds

A missing goh.db is no reading (nothing waived); a reading never raises into a land. The waiver
that reads them is lib/land_waive.py. Pinned by tests/test_main_red_waive.py.

GOH.MAINREDWAIVE: readings of main's tests at one sha, goh.db 0010 (`main_readings`,
`main_red_tests`): the idle full suite's run of main's tip (`record`, from `lib/idle_suite.py`
`reading`) and a probe of named tests on a clean checkout of main (`probe`); `red_at` is the nodes
red at a sha by the newest reading that ran them; `ensure`, before a land's gate, probes the idle
suite's red nodes at main's tip when no reading there ran them.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitrun
import goh_db  # lib/: the one door to goh.db
import main_red
import state_paths

PROBE_TIMEOUT = 600  # one run of main's red nodes on a clean checkout
DIR = "main-probe"  # under the queue: the probe's checkouts


def _repo(repo: str | Path) -> str:
    return os.path.realpath(str(repo))


def record(repo: str | Path, sha: str, source: str, ran: list[str] | str, red: list[str]) -> int:
    """One reading of main at SHA (module doc); its id."""
    at = time.time()
    with goh_db.transaction() as con:
        cur = con.execute(
            "INSERT INTO main_readings(repo, sha, source, ran, outcome, at) VALUES (?,?,?,?,?,?)",
            (state_paths.repo_key(_repo(repo)), sha, source,
             ran if ran == "*" else json.dumps(sorted(set(ran))),
             "red" if red else "green", at),
        )  # fmt: skip
        rid = int(cur.lastrowid or 0)
        con.executemany("INSERT OR IGNORE INTO main_red_tests(reading, node, at) VALUES (?,?,?)",
                        [(rid, n, at) for n in dict.fromkeys(red)])  # fmt: skip
    return rid


def _readings(repo: str | Path, sha: str) -> list[tuple[int, str, set[str]]]:
    """(id, ran, red nodes) of every reading at SHA, oldest first; [] without a goh.db."""
    try:
        with goh_db.known() as con:
            rows = con.execute("SELECT id, ran FROM main_readings WHERE repo=? AND sha=? "
                               "ORDER BY id", (state_paths.repo_key(_repo(repo)), sha)
                               ).fetchall()  # fmt: skip
            out = []
            for rid, ran in rows:
                red = {n for (n,) in con.execute(
                    "SELECT node FROM main_red_tests WHERE reading=?", (rid,))}  # fmt: skip
                out.append((int(rid), str(ran), red))
            return out
    except (OSError, sqlite3.Error):
        return []


def _ran(ran: str) -> set[str] | None:
    """The nodes a reading ran; None for the whole suite."""
    if ran == "*":
        return None
    try:
        return set(json.loads(ran))
    except ValueError:
        return set()


def red_at(repo: str | Path, sha: str) -> dict[str, int]:
    """{node: reading id} for each node red at SHA by the newest reading that ran it."""
    state: dict[str, int] = {}
    for rid, ran, red in _readings(repo, sha):
        nodes = _ran(ran)
        if nodes is None:  # the whole suite: what it did not see red was green
            state = dict.fromkeys(red, rid)
            continue
        for n in nodes:
            state.pop(n, None)
        state.update(dict.fromkeys(red & nodes, rid))
    return state


def covered(repo: str | Path, sha: str, nodes: list[str]) -> bool:
    """Whether the readings at SHA ran every one of NODES."""
    seen: set[str] = set()
    for _, ran, _red in _readings(repo, sha):
        got = _ran(ran)
        if got is None:
            return True
        seen |= got
    return set(nodes) <= seen


Runner = Callable[[Path, list[str]], tuple[int, str]]


def _pytest(wt: Path, nodes: list[str]) -> tuple[int, str]:
    import land_check  # lib/: a step in its own session under a deadline

    env = land_check._env(wt)
    argv = [*land_check.suite_argv(wt, env), "--", *nodes]
    rc, out, _ = land_check._run(argv, wt, env, PROBE_TIMEOUT)
    return rc, out


NO_VERDICT = {3, 4, 5, 124}  # pytest's interrupted, usage, no tests; the deadline


def probe(q: Path, repo: str | Path, sha: str, nodes: list[str], run: Runner | None = None
          ) -> int | None:  # fmt: skip
    """NODES on a clean checkout of main at SHA, recorded (module doc)."""
    wt = q / DIR / f"{os.getpid()}-{time.time_ns()}"
    wt.parent.mkdir(parents=True, exist_ok=True)
    try:
        add = gitrun.run_writer(("worktree", "add", "-q", "--detach", str(wt), sha),
                                repo=Path(repo), deadline_s=120)  # fmt: skip
        if add is None or add.returncode != 0:
            return None
        have = [n for n in nodes if (wt / n.split("::")[0]).is_file()]
        if not have:
            return None
        rc, out = (run or _pytest)(wt, have)
        red = [n for n in main_red.nodes_of(out) if n in have or n.split("[")[0] in have]
        if rc in NO_VERDICT or (rc != 0 and not red):
            return None  # no verdict: nothing recorded, nothing waived
        return record(repo, sha, "probe", have, red)
    finally:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            gitrun.run_writer(("worktree", "remove", "--force", str(wt)), repo=Path(repo),
                              deadline_s=120)  # fmt: skip
        shutil.rmtree(wt, ignore_errors=True)


def ensure(q: Path, repo: str | Path, say: Callable[[str], None] = print,
           run: Runner | None = None) -> int | None:  # fmt: skip
    """Main's idle-suite reds read at main's tip, when no reading there ran them (module doc)."""
    try:
        found = main_red.red(q, str(repo))
        nodes = (found or {}).get("nodes") or []
        if not nodes:
            return None
        head = gitrun.run_reader(("rev-parse", "HEAD"), repo=Path(repo), timeout=60)
        sha = head.stdout.strip() if head.returncode == 0 else ""
        if not sha or covered(repo, sha, nodes):
            return None
        rid = probe(q, repo, sha, nodes, run)
        red = sorted(n for n, r in red_at(repo, sha).items() if r == rid) if rid else []
        got = f"reading {rid}: {len(red)} red" if rid else "no verdict"
        say(f"main {sha[:8]}: the idle suite's {len(nodes)} red tests probed on it -- {got}")
        return rid
    except Exception as e:  # noqa: BLE001 -- a reading is never the reason a land fails
        say(f"main reading: not taken ({e.__class__.__name__}: {e})")
        return None
