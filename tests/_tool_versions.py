"""The house tool versions for tests, READ from gates/required_tools.tsv (GOH.CLASS.PARITY).

A test that fakes a tool at "the floor" or "the pin" asks here, never writes the number: a raise
in the manifest then moves every fake with it, and tests/test_tool_version_manifest.py is red on a
version literal written anywhere but the manifest.
"""

from __future__ import annotations

import sys
from pathlib import Path

_GATES = str(Path(__file__).resolve().parent.parent / "gates")
sys.path.insert(0, _GATES)
try:
    import required_tools as _rt
finally:
    sys.path.remove(_GATES)


def floor(tool: str) -> str:
    """`tool`'s floor (a pinned tool's pin counts as its floor)."""
    got = _rt.floors().get(tool) or _rt.pin(tool)
    assert got, f"gates/required_tools.tsv gives {tool} no version"
    return got


def pin(tool: str) -> str:
    got = _rt.pin(tool)
    assert got, f"gates/required_tools.tsv pins no {tool}"
    return got
