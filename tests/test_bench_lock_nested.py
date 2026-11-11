"""A measurement nested in a gate drains every gate but its own ancestors (GOH.BENCHNEST).

A consumer's P5 perf step runs inside ci_local inside a release: a claim there waited for the gates
it runs inside -- which cannot end before it does -- so its 180 s drain never once got the lock,
and the caller fell back to measuring with no lock at all, reaching into the private
`_bench_nested` to tell. The drain now skips the caller's ancestors, and `bench_lock_nested` is
the public "am I inside a gate" question.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from conftest import REPO_ROOT
from host_locks import scrubbed

LIB = REPO_ROOT / "lib" / "bench_lock.sh"


def _run(lock: Path, script: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    env = scrubbed(os.environ, ("GOH_BENCH_",))
    env["GOH_BENCH_LOCK_DIR"] = str(lock)
    return subprocess.run(
        ["bash", "-c", f'. "{LIB}"\n{script}'],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        check=False,
    )


# A gate that joins, then runs a child shell that claims the host with a 3 s drain.
NESTED = """
bench_lock_join "outer gate"
bash -c '. "{lib}"; bench_lock_nested && echo NESTED; bench_lock_exclusive "nested P5" 3 \\
    && echo CLAIMED; bench_lock_release'
: a command after the child, so bash does not exec it in the gate's own process
"""


def test_a_claim_nested_in_a_gate_does_not_wait_for_that_gate(tmp_path: Path) -> None:
    r = _run(tmp_path / "lock", NESTED.format(lib=LIB))
    assert "CLAIMED" in r.stdout, (r.stdout, r.stderr)


def test_bench_lock_nested_is_public_and_answers_both_ways(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    inside = _run(lock, NESTED.format(lib=LIB))
    assert "NESTED" in inside.stdout, (inside.stdout, inside.stderr)
    outside = _run(lock, "bench_lock_nested && echo NESTED || echo FREE")
    assert outside.stdout.strip() == "FREE", (outside.stdout, outside.stderr)


def test_a_nested_claim_still_waits_for_a_gate_that_is_not_its_ancestor(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    (lock / "gates").mkdir(parents=True)
    other = subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL)
    try:
        (lock / "gates" / str(other.pid)).write_text("pre-push\nelsewhere\n")
        r = _run(lock, NESTED.format(lib=LIB))
    finally:
        other.kill()
        other.wait()
    assert "CLAIMED" not in r.stdout, (r.stdout, r.stderr)
    assert f"{other.pid} (pre-push)" in r.stderr, r.stderr
