"""lib/load_reap.py `kill`: its verdict agrees with `ps` (GOH.BACKSTOPSTAMP).

Every process here is one the test started.
"""

from __future__ import annotations

import subprocess
import sys

import load_kit
import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))

import load_reap


@pytest.mark.load_sensitive
def test_a_kill_never_calls_a_process_gone_that_ps_still_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GOH.BACKSTOPSTAMP (v0.140.3's release gate, 'the orphan outlived the backstop'). The kill
    waited on `pid_owner.same`, which reads the kernel's `proc_pidinfo`; a process that has begun
    to exit stops answering it while `ps` still lists it, so under load the sweep reported a
    SIGKILLed orphan killed and the test's `ps` read found it there. The exit window is faked for
    this pid (tests/test_pid_owner.py `_exiting` measured it); the child ignores TERM, so the
    verdict has to come from the KILL, and it has to agree with `ps`."""
    script = "trap '' TERM; echo ready; while :; do sleep 1; done"
    proc = subprocess.Popen(["/bin/sh", "-c", script], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, text=True)  # fmt: skip
    try:
        assert proc.stdout is not None and proc.stdout.readline() == "ready\n"  # the trap is set
        held = load_reap.pid_owner.stamp(proc.pid)
        assert held is not None
        real = load_reap.pid_owner.birth
        calls = {"n": 0}

        def birth(pid: int, platform: str | None = None):
            if pid != proc.pid:
                return real(pid, platform)
            calls["n"] += 1
            return real(pid, platform) if calls["n"] <= 2 else None  # the stamp, the TERM

        monkeypatch.setattr(load_reap.pid_owner, "birth", birth)
        killed = load_reap.kill(proc.pid, grace=0.3)
        assert killed == (not load_kit.alive(proc.pid)), (killed, "ps disagrees with the verdict")
    finally:
        proc.kill()
        proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()
