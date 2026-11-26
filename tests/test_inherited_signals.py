"""A suite born deaf to INT or QUIT runs its tests at the defaults (GOH.RUSTOVERLAP).

A non-interactive bash starts its `cmd &` with INT and QUIT ignored, and a shell born with a
signal ignored can neither trap nor reset it. The land's rust gate runs the external suite as such
a part (gates/_rust_coverage.sh), so every shell a test started there was deaf to INT, and
tests/test_rust_overlap.py's SIGINT case hung to its timeout. The suite's processes put both back
(tests/_inherited_signals.py) before a test starts a child.
"""

from __future__ import annotations

import signal
import subprocess
import sys

from conftest import REPO_ROOT
from timing_bounds import patience

# Child shells that trap INT (QUIT) and send themselves one: 7 (8) if the trap ran, 0 if deaf.
TRAPS = ["bash", "-c", "trap 'exit 7' INT; kill -INT $$; exit 0"]
QUITS = ["bash", "-c", "trap 'exit 8' QUIT; kill -QUIT $$; exit 0"]


def _rc(argv: list[str]) -> int:
    done = subprocess.run(argv, stdin=subprocess.DEVNULL, check=False, timeout=patience(30))
    return done.returncode


def test_this_suite_runs_with_int_and_quit_at_their_defaults() -> None:
    assert signal.getsignal(signal.SIGINT) is not signal.SIG_IGN
    assert signal.getsignal(signal.SIGQUIT) is not signal.SIG_IGN
    assert (_rc(TRAPS), _rc(QUITS)) == (7, 8)


def test_a_process_born_deaf_to_int_and_quit_gives_its_children_both_back() -> None:
    probe = (
        "import signal, subprocess, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'tests')!r})\n"
        "import _inherited_signals\n"
        "_inherited_signals.restore()\n"
        f"print(subprocess.run({TRAPS!r}, stdin=subprocess.DEVNULL).returncode,"
        f" subprocess.run({QUITS!r}, stdin=subprocess.DEVNULL).returncode)\n"
    )
    deaf = ["bash", "-c", 'trap "" INT QUIT; exec "$0" -c "$1"', sys.executable, probe]
    got = subprocess.run(deaf, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                         check=False, timeout=patience(60))  # fmt: skip
    assert got.stdout.split() == ["7", "8"], got.stderr
