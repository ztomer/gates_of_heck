#!/usr/bin/env python3
"""suite_preflight.py -- what goh's suite refuses to start without, asked in ONE process
(GOH.PYHARNESS).

WHY. tools/pytest.sh asked each question of an interpreter of its own: eleven starts and five
re-runs of HEAD's export, 1.9 s of a one-file run before pytest (2026-11-25). Every question is
kept, and each module is still IMPORTED: a distribution whose metadata is right and whose import
fails (a wheel built for the python before this one) is the refusal the import was there for.

    suite_preflight.py [--table] [-- PYTEST ARGS...]      # run by tools/pytest.sh, from its tree

It answers on stdout, one `<key><TAB><value>` a line, and nothing else reaches that stream (an
import's own prints go to stderr):

    at MODULE        before each import, unbuffered: an import that kills the process is named
    refuse CODE WHY  the refusal (exit 1); what it found is on stderr
    table PATH       the suite's duration table (--table: a top-level run)
    plugins A,B      the goh-suite pins' pytest plugins, tools/pytest.sh's PYTEST_PLUGINS
    width N          a narrowed run's xdist workers (lib/suite_width.py); no line: the ceiling

What it refuses: a module of MODULES that does not import (GOH.PYTESTPIN, GOH.PLUGINPARITY; leaves
first, so a broken pluggy is not called a missing pytest); a pinned one at another release than
hosted CI installs (gates/required_pins.py); no `-m ruff`, or any ruff of the machine's that is not
goh's pin (gates/ruff_agree.py, all asked at once). The pins are this tree's manifest, as the suite
is: tools/pytest.sh starts it with GOH_LIVE=1. Pinned by tests/test_suite_preflight.py.

What goh's suite refuses to start without, asked in one process: every goh-suite module imports and
is at the manifest's pin, every ruff on the machine is goh's; and what the runner needs to know
(its duration table, the pins' pytest plugins, a narrowed run's width). Invoked by tools/pytest.sh.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import importlib
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# (the distribution pip installs, the module the suite imports): each is a goh-suite manifest row
# (gates/required_tools.tsv), and tests/test_required_tools_manifest.py reads them here.
MODULES = (
    ("pluggy", "pluggy"),  # suite: pytest's plugin manager (GOH.PLUGINPARITY)
    ("execnet", "execnet"),  # suite: xdist's transport
    ("pytest", "pytest"),  # suite: the runner itself (CI.PUBLIC2)
    ("pillow", "PIL"),  # suite: golden_core's (run 42930175911)
    ("numpy", "numpy"),  # suite: its parity tier
    ("coverage", "coverage"),  # suite: the coverage gate's own tests (run 42950563400)
    ("pytest-xdist", "xdist"),  # suite: no serial fallback to drift into (GOH.PYTESTPIN)
    ("pytest-cov", "pytest_cov"),  # suite: py_gate's coverage step (test_cwd_and_py_gate.py)
)
Say = Callable[[str, str], None]


def missing(dist: str, why: str = "") -> str:
    return (
        f"{dist} is not installed for the house interpreter {sys.executable}{why} -- "
        f'"{sys.executable}" -m pip install {dist}'
    )


def imports(say: Say, load: Callable[[str], object] = importlib.import_module) -> str:
    """Why a module of MODULES cannot be used, or ""."""
    for dist, module in MODULES:
        say("at", dist)
        try:
            load(module)
        except (Exception, SystemExit) as e:  # noqa: BLE001 -- whatever an import raises, it failed
            absent = isinstance(e, ModuleNotFoundError) and e.name == module
            return missing(dist, "" if absent else f" (its import: {type(e).__name__}: {e})")
    say("at", "")
    return ""


def pins() -> str:
    """Why this interpreter is not the manifest's, or "" (what is off is on stderr)."""
    import required_pins
    import required_tools

    rows = [r for r in required_tools.rows() if r["layer"] == "goh-suite"]
    if required_pins.held(rows) == 0:
        return ""
    cli = f"{sys.executable} {ROOT / 'gates' / 'required_tools.py'}"
    return (
        "this interpreter's goh-suite modules are not the manifest's pins (above) -- install them "
        f"with the pip line {cli} --layer goh-suite --install prints"
    )


def plugins() -> str:
    import required_pins
    import required_tools

    rows = [r for r in required_tools.rows() if r["layer"] == "goh-suite"]
    return ",".join(mod for mod, _dist in required_pins.manifest_plugins(rows))


def rufs() -> str:
    """Why the machine's ruff is not goh's, or ""."""
    import required_tools
    import ruff_agree

    every = ruff_agree.invocations(sys.executable, os.environ.get("PATH", ""))
    if every[0][2] is None:
        return missing("ruff")
    if ruff_agree.off_pin(sys.executable, required_tools.pin("ruff"), every) == 0:
        return ""
    return "the rufs on this machine are not goh's pinned ruff (above) -- install it, then rerun"


def main(argv: Sequence[str], say: Say) -> int:
    own, args = list(argv), []
    if "--" in own:
        at = own.index("--")
        own, args = own[:at], own[at + 1 :]
    if "--table" in own:
        try:
            from lib.fast_tests import cache

            say("table", str(cache()))
        except Exception as e:  # noqa: BLE001 -- a runner with no table name must not start
            print(f"suite_preflight: {type(e).__name__}: {e}", file=sys.stderr)
            say("refuse", "2 cannot resolve the suite's duration table (lib/fast_tests.py cache())")
            return 1
    for judge in (lambda: imports(say), pins):
        if why := judge():
            say("refuse", f"1 {why}")
            return 1
    try:
        say("plugins", plugins())
    except Exception as e:  # noqa: BLE001 -- as an unreadable manifest always was: a refusal
        print(f"suite_preflight: {type(e).__name__}: {e}", file=sys.stderr)
        say("refuse", "2 cannot read the goh-suite pins' pytest plugins (gates/required_pins.py)")
        return 1
    if why := rufs():
        say("refuse", f"1 {why}")
        return 1
    try:
        import suite_width

        n = suite_width.width(args, ROOT)
    except Exception as e:  # noqa: BLE001 -- a width is an economy: unknown, it is the ceiling
        print(f"suite_preflight: no width for this run: {type(e).__name__}: {e}", file=sys.stderr)
        n = None
    if n is not None:
        say("width", str(n))
    return 0


if __name__ == "__main__":
    # The root first, as `-m pytest` run from it has it; never lib/ ahead of site-packages.
    sys.path[0] = str(ROOT)
    sys.path += [str(ROOT / "gates"), str(ROOT / "lib")]
    out = os.dup(1)
    os.dup2(2, 1)  # an import's own print is no answer
    sys.exit(main(sys.argv[1:], lambda k, v: os.write(out, f"{k}\t{v}\n".encode()) and None))
