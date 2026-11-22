"""goh ship under a held gate slot refuses in one Kare line, never a traceback (GOH.SHIPORDERERR).

Reported 2026-11-17: goh ship wrapped in load.py (a gate slot held) raised a raw
lock_order.OrderError. The refusal names the held slot and says ship takes its own slots.
"""

from __future__ import annotations

import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "lib"))
import lock_order
import ship

WHY = "lock order: waiting on 'land' while holding 'gate_slot' breaks lib/lock_table.tsv"


def test_a_held_slot_is_one_err_line_and_a_nonzero_exit(monkeypatch, capsys) -> None:
    def plan(_args):
        raise lock_order.OrderError(WHY)

    monkeypatch.setattr(ship, "plan", plan)
    rc = ship.main(["--wait"])
    out = capsys.readouterr()
    text = out.out + out.err
    assert rc != 0
    assert "Traceback" not in text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 1 and "gate_slot" in lines[0] and "its own slots" in lines[0], text
