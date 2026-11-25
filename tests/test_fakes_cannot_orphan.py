"""No test fake can wait forever, and no session leader goes unowned (GOH.HOLDERORPHAN).

`goh unreaped-spawn` did not see the leak of 2026-11-19, and was not wrong: the fake gate's Popen
was stored on `self`, which that gate counts as a HANDOFF to an owner it cannot name, and the
owner (`Gate`) had a perfectly good `kill()` that nothing called when pytest died. A spawn rule
judges whether a reap EXISTS; this one judges the two properties that made the fake leak:

  * every shell or python fake that waits on a file leaves when its test process is gone and
    after a deadline (`orphan_kit.wait_for_file`, `wait_for_file_py`), never `until [ -e f ]`;
  * every file that starts a session leader (`start_new_session=True`) hands it to the suite's
    owner (`orphan_kit.adopt`, `load_kit.owning`/`own_pid`) or to a `killpg` of its own.

Both lists of exceptions are ratchets: an entry whose reason is gone fails, so the list only
shrinks. The class is closed when they hold nothing the kits could not.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
# Every tests/ module's text, globbed below (lib/declared_reads.py).
TREE_READS = ("tests/*.py",)
ME = Path(__file__).name

# A loop that waits on a file with no bound: shell `while [ ! -f F ]` / `until [ -e F ]`, or a
# python fake's `while not os.path.exists(F)` / `while not Path(...).exists()`. A line that carries
# its own bound (a counter test, `kill -0` on the test) is not one.
UNBOUNDED = re.compile(
    r"\b(?:while|until)\s+\[\s*!?\s*-[efd]\s"
    r"|\bwhile\s+not\s+(?:_?os\.path\.exists|Path\(sys\.argv)"
)
BODY = 5  # a loop's own bound is on its line or within the next few
BOUND = re.compile(r"-ge |-gt |kill -0|monotonic\(\)|give_up|\.passed\(\)")
OWNER = re.compile(r"adopt\(|owning\(|own_pid\(|Owned\(|killpg")

# file -> why an unbounded wait text in it is not a fake that can outlive its test.
WAIT_EXEMPT = {
    "orphan_kit.py": "the kit's own loops: they ARE the bounded form",
    "test_check_busy_loop.py": "string fixtures for the busy-loop linter, never run",
    "test_claude_load_guard.py": "string fixtures for the load guard's hook, never run",
    "test_monitor_guard.py": "string fixtures for the monitor guard's hook, never run",
    "test_wait_deadline.py": "string fixtures for the wait-deadline guard, never run",
    "test_load_burning.py": "a spinner of the house's shape, owned through load_kit on purpose",
}
# file -> why a session leader in it needs no owner of its own.
LEADER_EXEMPT = {
    "ps_table.py": "`ps -ax` under a CPU-stall judge, waited for in the same function",
    "suite_tmp.py": "the temp reaper, an orphan BY DESIGN: it exits when its pipe closes",
    "test_check_lock_order.py": "the keyword is a planted fixture's TEXT the checker reads; "
    "it spawns nothing",
}


def _files() -> list[Path]:
    return sorted(p for p in TESTS.glob("*.py") if p.name != ME)


def _unbounded(path: Path) -> list[int]:
    """Lines of an unbounded loop; the bound may sit in the loop's first lines (BODY of them)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        n
        for n, line in enumerate(lines, 1)
        if UNBOUNDED.search(line) and not BOUND.search("\n".join(lines[n - 1 : n - 1 + BODY]))
    ]


def test_no_fake_waits_on_a_file_without_a_bound():
    found = {
        p.name: lines for p in _files() if p.name not in WAIT_EXEMPT and (lines := _unbounded(p))
    }
    assert not found, (
        "an unbounded file-wait loop outlives a killed pytest (GOH.HOLDERORPHAN); use "
        f"orphan_kit.wait_for_file / wait_for_file_py: {found}"
    )


def test_the_wait_exemptions_are_all_still_earned():
    stale = [n for n in WAIT_EXEMPT if n != "orphan_kit.py" and not _unbounded(TESTS / n)]
    assert not stale, f"exempt, but no unbounded wait left in it: {stale}"


def test_every_session_leader_has_an_owner():
    unowned = [
        p.name
        for p in _files()
        if p.name not in LEADER_EXEMPT
        and "start_new_session=True" in (text := p.read_text(encoding="utf-8"))
        and not OWNER.search(text)
    ]
    assert not unowned, (
        f"a session leader with no owner and no killpg (orphan_kit.adopt): {unowned}"
    )


def test_the_leader_exemptions_are_all_still_earned():
    stale = [
        n
        for n in LEADER_EXEMPT
        if "start_new_session=True" not in (TESTS / n).read_text(encoding="utf-8")
        or OWNER.search((TESTS / n).read_text(encoding="utf-8"))
    ]
    assert not stale, f"exempt, but no longer needed: {stale}"


def test_the_pattern_sees_the_loop_that_leaked():
    """Calibration: the line the 2026-11-19 holder ran is a finding; the bounded forms are not."""
    leaked = 'while [ ! -f "{release}" ]; do sleep 0.05; done'
    assert UNBOUNDED.search(leaked) and not BOUND.search(leaked)
    assert UNBOUNDED.search("until [ -e x ]; do sleep 1; done")
    assert UNBOUNDED.search("    while not os.path.exists(p):")
    bounded = "until [ -e x ] || [ $i -ge 9 ]; do sleep 1; done"
    assert BOUND.search(bounded)
