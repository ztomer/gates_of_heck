"""A detached enqueue that is signalled to death names the processes alive at that moment
(lib/enqueue_witness.py, GOH.ENQUEUEVANISH).

The enqueuer runs detached (`start_new_session`, parent 1), and three of GOH.TIMINGDETECT's died
mid pre-step on 2026-11-23 with nothing in their log and their pre-step group still running: no
reader could say who sent the signal. The witness makes the death its own reading: the signal's
name and a process table taken while the sender is still alive.
"""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from orphan_kit import adopt
from timing_bounds import patience

CHILD = (
    "import sys, time; sys.path.insert(0, sys.argv[1]); import enqueue_witness; "
    "enqueue_witness.install(); print('ready', flush=True); time.sleep(60)"
)


@pytest.mark.load_sensitive  # processes, a signal, a wait bounded by patience
def test_a_signalled_enqueue_names_its_signal_and_the_sender(tmp_path: Path) -> None:
    log = tmp_path / "enqueue.log"
    with log.open("w") as err:
        child = adopt(subprocess.Popen([sys.executable, "-c", CHILD, str(REPO_ROOT / "lib")],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=err, text=True, env=hermetic_env(tmp_path),
                                       start_new_session=True))  # fmt: skip
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        marker = "witness-sender-0b7d"  # the sender's own command line carries it; `; true`
        # keeps bash from exec-ing its last command, which would replace that line with `sleep 5`
        sender = subprocess.Popen(
            ["bash", "-c", f"kill -TERM {child.pid}; sleep 5; true", marker],
            stdin=subprocess.DEVNULL,
        )
        try:
            code = child.wait(timeout=patience(30))
        finally:
            sender.kill()
            sender.wait()
    said = log.read_text()
    assert code == -signal.SIGTERM, said  # it still dies of the signal: a supersede still stops it
    assert "SIGTERM" in said, said
    assert marker in said, said  # the sender, alive while the table was read
