"""Run a Claude hook's `main()` IN this process, with the stdin, environment and exit code a
subprocess start would have given it (GOH.HOOKINPROC).

A hook test that only checks the DECISION (stdin JSON in, a refusal or silence out) does not need
the interpreter start: `discard_guard.py` and `agent_guard.py` were started ~500 times per suite for
~30 ms each before any of their code ran. `run_hook` is the same call minus the start: the hook's
module is imported once per worker, `os.environ` is REPLACED (not merged) with the env the
subprocess would have been given and put back afterwards (`mock.patch.dict`, so a raise restores it
too), stdin/stdout/stderr are the in-memory pair, and the result is a `CompletedProcess`, so a call
site changes by one line. An uncaught exception is the subprocess's: returncode 1, the traceback in
stderr, which the callers' `assert r.returncode == 0, r.stderr` shows.

xdist workers are separate processes, and a test runs alone inside one, so the swap cannot reach
another test; a test that starts threads reading `os.environ` during the call is not for this.
ONE real entry-point start per hook stays in its own test file (stdin JSON, exit code, output):
`__main__`, the shebang and the import path are what only a subprocess exercises.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import traceback
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

import timing_bounds

_LOADED: dict[Path, object] = {}


def _module(script: Path):
    """The hook's module, imported once per worker. Its directory is on `sys.path` while it loads,
    as it is when the script is run: the hooks import their siblings by bare name."""
    key = script.resolve()
    mod = _LOADED.get(key)
    if mod is None:
        spec = importlib.util.spec_from_file_location(f"_inproc_{key.stem}", key)
        assert spec is not None and spec.loader is not None, key
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(key.parent))
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.path.remove(str(key.parent))
        _LOADED[key] = mod
    return mod


def run_hook(
    script: Path, stdin: str, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """`script`'s `main()` on `stdin`, under `env` (None: this process's own, as if inherited)."""
    main = _module(script).main  # type: ignore[attr-defined]
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.ExitStack() as stack:
        if env is not None:
            stack.enter_context(mock.patch.dict(os.environ, dict(env), clear=True))
        stack.enter_context(mock.patch.object(sys, "stdin", io.StringIO(stdin)))
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(err))
        try:
            code = main()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        except Exception:  # noqa: BLE001 -- what a subprocess does: exit 1, the traceback on stderr
            err.write(traceback.format_exc())
            code = 1
    return subprocess.CompletedProcess([str(script)], code, out.getvalue(), err.getvalue())


def start_hook(
    script: Path, stdin: str, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """The REAL entry point: `script` started as the harness starts it (the interpreter, `__main__`,
    the import path, a real exit code). Only the one smoke test per hook calls this."""
    return subprocess.run(
        [sys.executable, str(script)], input=stdin, capture_output=True, text=True,
        env=None if env is None else dict(env), timeout=timing_bounds.patience(30), check=False,
    )  # fmt: skip
