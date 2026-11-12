"""The slot holder's handshake is read whole, or the gate fails naming it (GOH.CI107).

v0.107.0's hosted run failed test_a_push_waits_for_a_slot_and_its_step_limit_counts_from_the_slot:
the push exited 0 after waiting for its slot, and its log had no "taken after Ns waiting" line. The
shell read the handshake (`<idx> <slots> <waited> <path> <watch>`, ONE write from lib/gate_slot.py)
with `read -t 1` in a loop that polls the holder's liveness between reads. A timeout that fires
while `read` is part way through the line has already CONSUMED those bytes, and the loop's next
read started mid-line: measured 2026-11-12 on this Mac with bash 5.3, a 1 ms timeout split 404 of
2000 single-write lines; with the gate's 1 s timeout the window is the time `read` takes to drain
one line, which a loaded runner stretches. The tail of the line was then read as the fields --
`waited` got the watch name, `[ "$waited" -gt 0 ]` failed quietly, and the gate went on under a
"taken" line, a GOH_GATE_SLOT with no path, and no word of the wait.

Pinned here against a fake holder whose handshake ARRIVES in two parts across the timeout, the same
state, deterministically, under every bash the library is sourced by (it stays bash-3.2-safe):
  * the line is reassembled whole, and the wait is reported;
  * a handshake that is not five well-formed fields fails the gate, naming what it read.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from test_bash5_guard import _scope

REPO_ROOT = Path(__file__).resolve().parent.parent

# A holder that answers `nested` with "no" and writes its handshake in parts, a pause between them
# longer than the shell's read timeout; then holds until gate_slot_release ends it.
FAKE_HELPER = """
import os, sys, time
args = sys.argv[1:]
if args[0] == "nested":
    sys.exit(1)
fd = os.open(args[args.index("--handshake") + 1], os.O_WRONLY)
for i, part in enumerate(os.environ["FAKE_HANDSHAKE"].split("|")):
    if i:
        time.sleep(1.6)
    os.write(fd, part.encode())
os.close(fd)
null = os.open(os.devnull, os.O_RDWR)
for n in (0, 1, 2):
    os.dup2(null, n)
parent = os.getppid()
while os.getppid() == parent:
    time.sleep(0.1)
"""

GATE = """
set -euo pipefail
GOH="{goh}"; . "$GOH/tui/lib.sh"; . "$GOH/lib/gate_slot.sh"
GATE_SLOT_HELPER="{helper}"
trap gate_slot_release EXIT
gate_slot_acquire "repo"
echo "SLOT=[$GOH_GATE_SLOT]"
"""


def _bashes() -> list[str]:
    """Every bash on this machine the library may be sourced by; one per version."""
    seen: dict[str, str] = {}
    for cand in ("/opt/homebrew/bin/bash", "/usr/bin/bash", "/bin/bash"):
        if os.access(cand, os.X_OK):
            v = subprocess.run(
                [cand, "-c", 'printf %s "${BASH_VERSINFO[0]}"'],
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            ).stdout
            seen.setdefault(v, cand)
    return sorted(seen.values())


def _acquire(tmp_path: Path, bash: str, handshake: str) -> subprocess.CompletedProcess:
    helper = tmp_path / "fake_holder.py"
    helper.write_text(FAKE_HELPER)
    env = dict(os.environ, GOH_DIR=str(REPO_ROOT), NO_COLOR="1", FAKE_HANDSHAKE=handshake)
    env["GOH_GATE_SLOT_DIR"] = str(tmp_path / "slots")
    env["GOH_GATE_SLOTS"] = "1"
    env.pop("GOH_GATE_SLOT", None)
    return subprocess.run(
        [bash, "-c", GATE.format(goh=REPO_ROOT, helper=helper)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


@pytest.mark.parametrize("bash", _bashes())
def test_a_handshake_split_across_the_read_timeout_is_read_whole(tmp_path, bash):
    slot = tmp_path / "slots" / "slot-0"
    got = _acquire(tmp_path, bash, f"0 1| 3 {slot} kqueue\n")
    out = got.stdout + got.stderr
    assert got.returncode == 0, out
    assert "gate slot 1/1 taken after 3s waiting" in got.stdout, out
    assert "owner watch: kqueue" in got.stdout, out
    assert f"SLOT=[{slot} " in got.stdout, out


@pytest.mark.parametrize("bash", _bashes())
def test_a_malformed_handshake_fails_the_gate_naming_it(tmp_path, bash):
    got = _acquire(tmp_path, bash, "0 one 3\n")
    assert got.returncode == 1, got.stdout + got.stderr
    assert "SLOT=" not in got.stdout, got.stdout
    assert "handshake" in got.stderr and "'0 one 3'" in got.stderr, got.stderr


# THE CLASS: a timed `read` of more than one character, in any bash file goh tracks. Its timeout can
# fire after bytes are consumed, and the caller's retry reads the rest as a new line. One character
# at a time (`-n 1`) is consumed whole or not at all.
TIMED_READ = re.compile(r"\bread\s+(?:-[A-Za-z]+\s+(?:\d+\s+)?)*?-[A-Za-z]*t\s")


def timed_line_reads(path: str, text: str) -> list[str]:
    return [
        f"{path}:{i}: {line.strip()}"
        for i, line in enumerate(text.splitlines(), 1)
        if not line.lstrip().startswith("#") and TIMED_READ.search(line) and "-n 1" not in line
    ]


def test_the_class_check_sees_a_timed_line_read():
    old = "    while ! read -r -t 1 idx total waited path mech <&7; do\n"
    assert timed_line_reads("lib/x.sh", old) == ["lib/x.sh:1: " + old.strip()]
    assert timed_line_reads("lib/x.sh", "read -rt 5 line\n")
    assert timed_line_reads("lib/x.sh", "        if IFS= read -r -n 1 -t 1 c <&7; then\n") == []
    assert timed_line_reads("lib/x.sh", "read -r line  # -t is not here\n") == []


def test_no_tracked_bash_file_reads_a_line_with_a_timeout():
    files = _scope()
    bad = [
        b for f in files for b in timed_line_reads(f, (REPO_ROOT / f).read_text(errors="replace"))
    ]
    assert not bad, "a timed read of a whole line loses what a timeout consumed:\n" + "\n".join(bad)
