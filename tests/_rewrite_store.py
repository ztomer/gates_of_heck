"""pytest's rewritten test files, kept between runs (GOH.PYHARNESS).

WHY. pytest rewrites every test file's asserts and keeps the result beside it, in `__pycache__`.
The agent shells export PYTHONDONTWRITEBYTECODE=1, so nothing was ever kept: `--collect-only` of
809 files spent 8 of its 11.5 CPU seconds rewriting (17.8 s of wall, 4.6 s once kept; load 34-36,
2026-11-25), and in a whole suite every xdist worker did the same again. Writing beside the files is
no answer (a worktree per lane, each its own cold cache, and the tree a suite may not move), so the
rewrite of a file under this checkout's tests/ lives in ONE store of the machine's,
`~/.cache/goh/suite/rewrite/<pytest's own tag>/<stem>.<digest>.pyc`:

* keyed by the source's CONTENT (and the two settings that change a rewrite: pytest's
  `enable_assertion_pass_hook` and `-O`), so worktrees share it and none overwrites another;
* in pytest's own pyc format, read by pytest's own `_read_pyc`, which checks the source's hash
  again and points the code at this file's path;
* written by ONE process of a run -- the controller, or worker gw0 -- and never inside a test
  (PYTEST_CURRENT_TEST: a test's own disk is counted, and a suite a test starts only reads);
* pruned as it is written: the KEEP newest of a stem, and no other tag's (an older pytest's).

Every failure is the rewrite pytest would have done: an unreadable store, another process's prune, a
file outside tests/. pytest is pinned exactly (gates/required_tools.tsv); a release that renames
what is read here fails tests/test_rewrite_store.py, and the suite runs on, unkept.
"""

from __future__ import annotations

import contextlib
import glob
import hashlib
import importlib.util
import os
import shutil
import sys
from pathlib import Path
from types import CodeType

from _pytest.assertion import rewrite
from _pytest.assertion.rewrite import AssertionRewritingHook, assertstate_key

TESTS = Path(__file__).resolve().parent
KEEP = 4
_NATIVE = AssertionRewritingHook.exec_module


def store() -> Path:
    """The machine's store, in the suite's corner of the REAL cache (suite_cache_guard.sh)."""
    home = os.environ.get("GATES_OF_HECK_SUITE_REAL_HOME") or os.path.expanduser("~")
    return Path(home) / ".cache" / "goh" / "suite" / "rewrite" / rewrite.PYTEST_TAG


def writes(config) -> bool:
    """One process of a run, outside any test."""
    worker = getattr(config, "workerinput", None)
    one = worker is None or worker.get("workerid") == "gw0"
    return one and "PYTEST_CURRENT_TEST" not in os.environ


def kept(root: Path, fn: Path, source: bytes, config) -> Path:
    """Where the rewrite of `source` is kept: its content and what else decides the rewrite."""
    shape = (bool(config.getini("enable_assertion_pass_hook")), sys.flags.optimize)
    digest = hashlib.blake2b(source + repr(shape).encode(), digest_size=16).hexdigest()
    return root / f"{fn.stem}.{digest}{rewrite.PYC_EXT}"


def _prune(pyc: Path, stem: str) -> None:
    """The KEEP newest rewrites of a stem (a digest holds no dot), and only this pytest's."""
    same = [p for p in pyc.parent.glob(f"{glob.escape(stem)}.*") if p.name.count(".") == 2]
    for old in sorted(same, key=lambda p: p.stat().st_mtime, reverse=True)[KEEP:]:
        old.unlink()


def _make(root: Path) -> None:
    if not root.is_dir():
        root.mkdir(parents=True)
        # Another pytest's or interpreter's, by the shape of its tag: nothing reads it now.
        for other in root.parent.glob("*-pytest-*"):
            if other != root:
                shutil.rmtree(other, ignore_errors=True)


def code(hook: AssertionRewritingHook, fn: Path, root: Path, write: bool) -> CodeType:
    """The rewritten code of `fn`: the store's, else pytest's rewrite (kept when `write`)."""
    state = hook.config.stash[assertstate_key]
    source = fn.read_bytes()
    pyc = kept(root, fn, source, hook.config)
    co = rewrite._read_pyc(fn, pyc, state.trace)
    if co is not None:
        return co
    source_hash, co = rewrite._rewrite_test(fn, hook.config)
    if write and source_hash == importlib.util.source_hash(source):  # else it changed under us
        hook._writing_pyc = True
        try:
            with contextlib.suppress(OSError):
                _make(root)
                rewrite._write_pyc(state, co, source_hash, pyc)
                _prune(pyc, fn.stem)
        finally:
            hook._writing_pyc = False
    return co


def _exec_module(self: AssertionRewritingHook, module) -> None:
    fn = Path(module.__spec__.origin)
    if TESTS not in fn.parents:
        return _NATIVE(self, module)
    self._rewritten_names[module.__name__] = fn
    co = code(self, fn, store(), writes(self.config))
    exec(co, module.__dict__)  # noqa: S102 -- what pytest's own loader does with it
    return None


def install() -> bool:
    """Every rewrite of a tests/ file from here on goes through the store; False when this pytest
    has not what is read here, which leaves pytest's own."""
    if not all(hasattr(rewrite, n) for n in ("_read_pyc", "_rewrite_test", "_write_pyc")):
        return False
    AssertionRewritingHook.exec_module = _exec_module
    return True
