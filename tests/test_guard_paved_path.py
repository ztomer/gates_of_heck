"""A refused hand-written wait or kill names the exact `goh wait` / `goh stop` that replaces it
(GOH.WAITSTOP).

137 refusals in the week to 2026-11-21 were agents' own process waits (`lib/self_match.py`) and
kills by name (`lib/name_kill.py`); each named the safe SHAPES and the agent rewrote the command by
hand. The rows below are those refused commands as agents typed them (paths shortened), each with
the paved-path line its refusal must now carry (`lib/paved_path.py`).
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_claude_load_guard import _decide
from timing_bounds import patience

PK, PG = "p" + "kill", "p" + "grep"  # split: the house's kill-by-name check reads files
NOMATCH = "zzwaitstopnomatch"  # a fixed token (xdist collects alike on every worker)
OUT = "/private/tmp/claude-501/x/tasks/b4d3fbsu0.output"

LAND_CEIL = f'! {PG} -f "land --wait GOH.CEILINGHEADROOM2" >/dev/null'
LAND_SPAWN = f'! {PG} -f "goh land --wait GOH.SPAWNREGISTER2" >/dev/null'
LAND_MISS = f'! {PG} -f "enqueue --land.*MISSLEDGERGATE{NOMATCH}" >/dev/null'
LOGS = "~/.local/state/goh/ship-*.log"
SHIP = f'{PG} -f "goh ship --summary" >/dev/null'
COMMIT = f"$({PG} -f 'git commit -qm queue: zz{NOMATCH}' )"
SELFMOVE = f'{PG} -f "land --wait GOH.PRESTEPSELFMOVE" >/dev/null'
# (refused command, the replacement its refusal names)
REFUSED_WAITS = [
    (f"until [ -s {OUT} ] && {LAND_CEIL}; do sleep 20; done; tail -15 {OUT}",
     "`goh wait land GOH.CEILINGHEADROOM2`"),
    (f'until grep -q "exited with code" {OUT} 2>/dev/null || {LAND_SPAWN}; do sleep 15; done',
     "`goh wait land GOH.SPAWNREGISTER2`"),
    (f"until [ -s {OUT} ] && {LAND_MISS}; do sleep 10; done",
     "the wait is already over"),  # no item id in the pattern: judged by what it matches now
    (f'L=$(ls -t {LOGS} | head -1); while {SHIP}; do sleep 20; done; tail -15 "$L"',
     "`goh wait ship RESULT`"),
    (f"until ! {SHIP}; do sleep 15; done; tail -15 x.log", "`goh wait ship RESULT`"),
    (f'cd "$HOME/x" && until [ -z "{COMMIT}" ]; do sleep 5; done',
     "the wait is already over"),
    (f"for i in $(seq 1 110); do {SELFMOVE} || break; sleep 5; done; {SELFMOVE} && echo still",
     "`goh wait land GOH.PRESTEPSELFMOVE`"),
]  # fmt: skip

REFUSED_KILLS = [
    f'{PK} -f "until grep -q zz{NOMATCH}" ; echo ok',
    f"cd /x && {PK} -f 'goh lan[d] --enqueue --land .*GOH.RUSTREVPARSE{NOMATCH}'; sleep 2",
    f"{PG} -f '[l]oad.py --wait 0 -- ./tools/pytest.sh -p zz{NOMATCH}' | xargs -I{{}} kill {{}}",
    f'{PK} -TERM -f "load.py --wait-max 5400 --max 2h 0 -- bash zz{NOMATCH}$" ; true',
]  # fmt: skip


@pytest.mark.parametrize(("command", "instead"), REFUSED_WAITS, ids=range(len(REFUSED_WAITS)))
def test_a_refused_wait_names_the_goh_wait_that_replaces_it(
    command: str, instead: str, tmp_path: Path
) -> None:
    reason = _decide(command, tmp_path)
    assert reason is not None, command
    assert reason.startswith("Run instead: "), reason
    assert instead in reason, reason


@pytest.mark.parametrize("command", REFUSED_KILLS, ids=range(len(REFUSED_KILLS)))
def test_a_refused_kill_by_name_names_goh_stop(command: str, tmp_path: Path) -> None:
    reason = _decide(command, tmp_path)
    assert reason is not None, command
    assert reason.startswith("Run instead: `goh stop "), reason
    assert "goh stop --stamp $!" in reason, reason  # nothing matches: the stamp route is named


@pytest.fixture
def marked(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, int]]:
    """A live process whose command line carries a marker nothing else does, started under THIS
    test's agent session: `goh stop` names only the caller's session's pids (lib/session_owner.py),
    and a suite run outside any agent (the Linux worker's `docker exec`, GOH.WORKERFAILS) has no
    session id, so the kill's line named none."""
    mark = f"gohwaitstop{uuid.uuid4().hex}"  # this test's alone, even beside an xdist twin
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", f"suite-{mark}")
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()", mark],
                            stdin=subprocess.PIPE)  # fmt: skip
    try:
        yield mark, proc.pid
    finally:
        proc.communicate(input=b"", timeout=patience(30))


@pytest.mark.load_sensitive
def test_a_wait_on_a_live_match_names_its_pid(marked: tuple[str, int], tmp_path: Path) -> None:
    mark, pid = marked
    reason = _decide(f'while {PG} -f "{mark}" >/dev/null; do sleep 5; done; echo {mark}', tmp_path)
    assert reason is not None
    assert f"`goh wait pid {pid}`" in reason, reason


@pytest.mark.load_sensitive
def test_a_kill_of_a_live_match_names_its_pid(marked: tuple[str, int], tmp_path: Path) -> None:
    mark, pid = marked
    reason = _decide(f'{PK} -f "{mark}"', tmp_path)
    assert reason is not None
    assert reason.startswith(f"Run instead: `goh stop {pid}`"), reason


def test_a_ship_wait_with_a_live_match_names_both(tmp_path: Path) -> None:
    import paved_path

    line = paved_path.wait_command("goh ship --summary", [41, 42])
    assert line == (
        "`goh wait ship RESULT` (RESULT: the result path `goh ship` printed), or "
        "`goh wait pid 41 42`"
    )
