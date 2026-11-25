"""The work a fixture did in this phase, as seconds its setup ceiling makes room for, by COUNT.

A setup ceiling over a kept store that may be cold judged the cache and the load, not the test
(GOH.TEMPLEAKSETUP): tests/_tree_lint.py's first setup is 0.14 s warm and, cold, every shell file
of the tree through a parallel shellcheck -- 80.8 CPU s over 114 files. Its wall stretches by the
cores it cannot get, which neither the run queue nor one stretch sample models: 30.5 s against 20 s
at load 30, 83 s and 91 s in the idle suite, no code changed. The fixture says how many items it
had to do and what one costs; the ceiling grows by that and by nothing else, so a fixture that did
no work keeps the plain ceiling and one that hangs is red at any count.
"""

from __future__ import annotations

_SAID = [0.0]


def did(items: int, each_s: float) -> None:
    """This phase did ITEMS units of work of EACH_S CPU seconds (read by tests/_drift_guard.py)."""
    _SAID[0] += max(0, items) * each_s


def take() -> float:
    """The seconds said since the last phase, and forget them: one phase's work is its own."""
    said, _SAID[0] = _SAID[0], 0.0
    return said
