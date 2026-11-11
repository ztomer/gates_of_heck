"""No test builds a child's environment by spreading `os.environ` (GOH.ENVLEAK, a ratchet).

`{**os.environ, ...}`, `os.environ.copy()` and `dict(os.environ)` hand a child whatever the CALLER
exported. Run from a hook or a gate, that is the push gate's GOH_CHANGE_BASE (test_crates_behind's
red arm read green, GOH.RUNNERPIN, 2026-11-11), a hook's GIT_DIR (a fixture's repo init
re-initialises the real repository, contract #12), or goh ship's GOH_SHIP_*. The one helper is
`conftest.hermetic_env` (no inherited GOH_* but the locations it names; `drop_git=True` drops
GIT_* too), and what a test needs is put back by name.

The seed, tests/env_spread_seed.json, is exactly the spreads each file held when this landed: a
NEW spread is red, and so is an entry larger than its file's count (a stale entry hides the next
one), so the list only shrinks (close-the-class).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SEED = TESTS / "env_spread_seed.json"


def _is_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def spreads(source: str) -> list[int]:
    """The line of every `os.environ` spread in `source`."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            if any(
                k is None and _is_environ(v) for k, v in zip(node.keys, node.values, strict=True)
            ):
                found.append(node.lineno)
        elif isinstance(node, ast.Call):
            f = node.func
            copy = isinstance(f, ast.Attribute) and f.attr == "copy" and _is_environ(f.value)
            wrap = isinstance(f, ast.Name) and f.id == "dict" and node.args
            if copy or (wrap and _is_environ(node.args[0])):
                found.append(node.lineno)
    return sorted(found)


def census() -> dict[str, int]:
    out = {}
    for path in sorted(TESTS.rglob("*.py")):
        n = len(spreads(path.read_text(encoding="utf-8")))
        if n:
            out[path.relative_to(TESTS).as_posix()] = n
    return out


def test_the_detector_sees_every_spelling_and_nothing_else():
    """Calibration: each spelling is one finding; a scrubbed build and a plain read are none."""
    src = (
        "import os\n"
        "a = {**os.environ, 'X': '1'}\n"
        "b = os.environ.copy()\n"
        "c = dict(os.environ)\n"
        "d = os.environ.get('HOME')\n"
        "e = {k: v for k, v in os.environ.items() if not k.startswith('GOH_')}\n"
        "f = hermetic_env(X='1')\n"
    )
    assert spreads(src) == [2, 3, 4]


def test_no_test_spreads_os_environ_beyond_the_seed():
    seed = json.loads(SEED.read_text(encoding="utf-8"))["files"]
    now = census()
    grown = {f: n for f, n in now.items() if n > seed.get(f, 0)}
    assert not grown, f"os.environ spread into a child env (use conftest.hermetic_env): {grown}"
    stale = {f: (n, now.get(f, 0)) for f, n in seed.items() if now.get(f, 0) < n}
    assert not stale, f"lower these seed entries to the file's count (seed, now): {stale}"
