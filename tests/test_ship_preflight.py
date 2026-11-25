"""lib/ship_preflight.py: what a preflight that passed warned of is said (GOH.PINSWEEP), and one
that refuses stops the ship with its last lines; its command is a fake that writes its log."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import ship_preflight
from bounded_run import Outcome


def asked(monkeypatch, said: str, code: int) -> None:
    def step(_argv, _timeout, _grace, _label, log: str) -> Outcome:
        Path(log).write_text(said)
        return Outcome(code, [], False)

    monkeypatch.setattr(ship_preflight.bounded_run, "run_step", step)
    args = argparse.Namespace(summary="", item="")
    ship_preflight.run_preflights(Path("/p/x"), [("GOH_SHIP_PREFLIGHT", "c")], "1.0.0", args, 9)


def test_a_passing_preflights_warnings_are_said_and_nothing_else(monkeypatch, capsys) -> None:
    asked(monkeypatch, "→ looked\n⚠ held: its pin waits\n✓ fine\n", 0)
    said = "".join(capsys.readouterr())
    assert "⚠ held: its pin waits" in said and len(said.splitlines()) == 1, said


def test_a_refusing_preflight_says_its_last_lines(monkeypatch, capsys) -> None:
    with pytest.raises(ship_preflight.Refused, match="no row for it"):
        asked(monkeypatch, "⚠ held: its pin waits\nno row for it\n", 1)
    assert "held" not in "".join(capsys.readouterr())
