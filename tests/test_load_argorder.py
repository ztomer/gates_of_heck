"""An option after N is refused, naming the order (GOH.LOADARGORDER).

`load.py 0 --wait 0 -- cmd` ran taskpolicy with `--wait` and exited 64 (CIP.GAMEASSETFACTORY):
argparse's REMAINDER took everything after N as the command.
"""

from __future__ import annotations

import subprocess
import sys

import timing_bounds
from conftest import hermetic_env
from load_kit import TOOL


def test_an_option_after_n_is_refused_naming_the_order(tmp_path) -> None:
    r = subprocess.run([sys.executable, str(TOOL), "--dir", str(tmp_path), "0", "--wait", "--",
                        "true"], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=timing_bounds.patience(60.0), check=False,
                       env=hermetic_env())  # fmt: skip
    assert r.returncode == 2, r.stdout + r.stderr
    assert "--wait" in r.stderr and "before N" in r.stderr, r.stderr
