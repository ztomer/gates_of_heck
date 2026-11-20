"""No test patches the time module's clock for the whole process (GOH.LANDQWINFLAKE).

tests/_this_thread.py says why: a fake `time.sleep`/`time.time`/`time.monotonic` set with
`monkeypatch.setattr(time, ...)` -- or through any module's `.time`, the same object -- is every
thread's. Such a patch is refused unless its value is `this_thread(fake, real)`; better still, the
code takes its clock as an argument.

What this READS, in every tests/**/*.py but tests/fixtures/: a `setattr` call (monkeypatch's or the
builtin) whose target is `time` or `<anything>.time` and whose name is a clock function, or whose
dotted target string is `time.<clock>`; and `patch("time.<clock>")`. What it does NOT read: a patch
of the clock a module imported by name (`from time import sleep`), which is that module's alone.
"""

from __future__ import annotations

import ast

from conftest import REPO_ROOT

TESTS = REPO_ROOT / "tests"
CLOCK = {"sleep", "time", "time_ns", "monotonic", "monotonic_ns", "perf_counter",
         "perf_counter_ns", "process_time", "thread_time"}  # fmt: skip


def _is_time(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == "time") or (
        isinstance(node, ast.Attribute) and node.attr == "time"
    )


def _wrapped(node: ast.AST | None) -> bool:
    f = node.func if isinstance(node, ast.Call) else None
    return isinstance(f, ast.Name) and f.id == "this_thread"


def _dotted(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.partition(".")[0] == "time"
        and node.value.partition(".")[2] in CLOCK
    )


def findings(source: str) -> list[int]:
    """The line of every process-wide patch of the time module's clock in SOURCE."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
        args = node.args
        if name == "setattr" and len(args) >= 3 and _is_time(args[0]):
            hit = isinstance(args[1], ast.Constant) and args[1].value in CLOCK
            if hit and not _wrapped(args[2]):
                found.append(node.lineno)
        elif name == "setattr" and len(args) >= 2 and _dotted(args[0]):
            if not _wrapped(args[1]):
                found.append(node.lineno)
        elif name == "patch" and args and _dotted(args[0]):
            found.append(node.lineno)
    return found


def census() -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for path in sorted(TESTS.rglob("*.py")):
        rel = path.relative_to(TESTS).as_posix()
        if rel.startswith("fixtures/") or rel == "test_no_global_clock_patch.py":
            continue
        if lines := findings(path.read_text(encoding="utf-8")):
            out[rel] = lines
    return out


def test_the_reader_sees_every_spelling_of_a_process_wide_clock_patch() -> None:
    src = (
        "import time\n"
        "from unittest import mock\n"
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(time, 'sleep', lambda s: None)\n"  # 4
        "    monkeypatch.setattr(mod.time, 'monotonic', lambda: 1.0)\n"  # 5
        "    monkeypatch.setattr('time.time', lambda: 1.0)\n"  # 6
        "    setattr(time, 'time', lambda: 1.0)\n"  # 7
        "    mock.patch('time.sleep')\n"  # 8
        "    monkeypatch.setattr(time, 'sleep', this_thread(lambda s: None, time.sleep))\n"
        "    monkeypatch.setattr(mod, 'sleep', lambda s: None)\n"
        "    monkeypatch.setattr(time, 'tzname', ('A', 'B'))\n"
        "    monkeypatch.setattr(mod.time_source, 'sleep', lambda s: None)\n"
    )
    assert findings(src) == [4, 5, 6, 7, 8]


def test_no_test_patches_the_clock_of_every_thread() -> None:
    found = census()
    assert not found, (
        "a test patches the time module's clock for every thread of the worker: pass the code a "
        f"clock, or patch with tests/_this_thread.py this_thread(fake, real) -- file: lines {found}"
    )
