"""agent_guard.faults: the `Faults seen:` line as a list (split from test_claude_agent_guard.py).

GOH.FAULTSPLIT: only a TOP-LEVEL ';' separates faults; the rest are recorded exactly as written.
"""

from __future__ import annotations

import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "hooks" / "claude"))
import agent_guard


def test_faults_none_is_an_empty_list() -> None:
    assert agent_guard.faults("Status: landed\nFaults seen: none") == []


def test_faults_several_are_split_on_semicolons() -> None:
    report = "Status: landed\nabc1234 x\nFaults seen: WARN disk on .33 health; /srv/t not g+w (.90)"
    assert agent_guard.faults(report) == ["WARN disk on .33 health", "/srv/t not g+w (.90)"]


def test_faults_are_recorded_exactly_as_written() -> None:
    """G15: a leading backtick was stripped from the first fault (only whitespace is trimmed)."""
    report = "Faults seen: `tools/land.sh` printed landed; second `one`\n"
    assert agent_guard.faults(report) == ["`tools/land.sh` printed landed", "second `one`"]
    assert agent_guard.faults("Faults seen:   `x` at y  ") == ["`x` at y"]


def test_faults_a_semicolon_inside_brackets_or_quotes_is_not_a_separator() -> None:
    """GOH.FAULTSPLIT: the two real lines of 2026-11-09 each became two rows, which the loop then
    triaged as separate faults. Only a TOP-LEVEL ';' separates faults."""
    a = "a targeted tools/pytest.sh run ... (only in my side run; the land gate passed)"
    b = '... ("cannot lock ref HEAD"; tree-stamp refusal ...)'
    assert agent_guard.faults(f"Faults seen: {a}") == [a]
    assert agent_guard.faults(f"Faults seen: {b}") == [b]
    assert agent_guard.faults(f"Faults seen: {a}; {b}") == [a, b]
    assert agent_guard.faults("Faults seen: x [a; b] y; `c; d`; \"e; f\" g; h") == [
        "x [a; b] y", "`c; d`", '"e; f" g', "h",
    ]  # fmt: skip


def test_faults_an_apostrophe_or_an_unbalanced_opener_does_not_swallow_the_rest() -> None:
    """Prose has apostrophes ("don't") and agents drop a closer: neither may merge two faults
    into one, nor lose one -- an unbalanced line falls back to the plain split."""
    assert agent_guard.faults("Faults seen: it doesn't build; second") == [
        "it doesn't build",
        "second",
    ]
    assert agent_guard.faults("Faults seen: opened (never closed; second") == [
        "opened (never closed",
        "second",
    ]
    assert agent_guard.faults("Faults seen: closed) early; second") == ["closed) early", "second"]


def test_faults_a_backticked_none_is_still_none() -> None:
    assert agent_guard.faults("Faults seen: `none`") == []


def test_faults_missing_line_is_null_not_none() -> None:
    assert agent_guard.faults("Status: landed\nall good") is None
