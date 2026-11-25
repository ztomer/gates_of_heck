"""The test modules whose verdict reads the owner's LIVE loop checkout, an input outside the tree
(GOH.REUSEOFFTEST).

A reuse key names the tree and the tools. These modules read the loop's live queue and rules, which
move between a land's pre-step and its gate, so a pass reused there is a claim no key backs: on
2026-11-23 one passed, the rules moved, and the `test` reuse path turned itself off for every land.
So the pre-step chooses them on every diff (tools/affected_tests.py ALWAYS: nothing carries them)
and no same-tree pass of theirs is reused (tests/_land_audit.py `audited`). LIVE is held to the tree
by tests/test_live_reads.py, which derives the set from each module's code.
"""

from __future__ import annotations

import ast

LIVE = (
    "tests/test_agent_stalls_loop.py",
    "tests/test_filer_live_rules.py",
    "tests/test_queue_read.py",
)


def live(nodeid: str) -> bool:
    """The node's module reads the live loop checkout: its pass is never reused."""
    return nodeid.split("::", 1)[0] in LIVE


def reads_live(text: str) -> bool:
    """The module's code names the caller's REAL home (suite_home.REAL_HOME, which the suite's own
    home hides) and the loop's checkout: the only way a test reaches the live one."""
    real = loop = False
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Attribute) and node.attr == "REAL_HOME":
            real = real or (isinstance(node.value, ast.Name) and node.value.id == "suite_home")
        if isinstance(node, ast.ImportFrom) and node.module == "suite_home":
            real = real or any(a.name == "REAL_HOME" for a in node.names)
        loop = loop or (isinstance(node, ast.Constant) and node.value == ".loop")
    return real and loop
