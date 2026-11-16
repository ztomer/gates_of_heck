"""A land's pre-step verdicts, reused by its gate: a green land runs each test once.

GOH.LANDPREDUP. tools/land.sh runs the tests its diff chooses BEFORE the land lock (GOH_LAND_PRE,
GOH.TREETESTSFIRST), then the full gate runs the whole suite, those tests again among it: 213-565 s
of pre-step paid twice over on every green land (2026-11-16). So the pre-step RECORDS what it
passed and the gate's suite DESELECTS it:

  * GOH_LAND_VERDICTS_RECORD=FILE (the pre-step's run): the controller writes FILE at session end,
    {"identity": ..., "passed": {node id: whether it ran the native binary}}; a node passed when
    none of its phases failed and its call passed (a skip is no verdict);
  * GOH_LAND_VERDICTS_REUSE=FILE (the gate's run): a test the record passed is deselected -- only
    when the record's identity is THIS checkout's (lib/gate_identity.py `suite`: the commit's tree
    and the tools, a dirty checkout having none). A land rebased under its lock gates another tree,
    so it reuses nothing, and says so.

COVERAGE. goh's suite runs in the gate inside the coverage step, against the INSTRUMENTED binary
(GOH_COV_RUST_EXTERNAL; GOH_COVERAGE_BIN_DIR and LLVM_PROFILE_FILE set): there a test's run is
also coverage the crate's floor counts, so a test that ran the native binary (asked for
`native_goh`, or reached it through `native_goh_path()`, which marks the test) is never deselected
-- only the ones whose verdict is all they give. Outside the coverage step every passed test is
reusable, the plain run of a reused Rust measurement included (GOH.RUSTCOVSKIP: its binary
directory is named, no profile is).

Both keys are set by tools/land.sh for one land and are verdict-free (gates/verdict_free_keys.txt):
a path to one land's record is bookkeeping, and the proof a gate records is the same with or
without it, because a reused verdict is a verdict on the same tree. A hermetic child (a fixture
suite) drops every GOH_ key, so no nested suite reuses anything.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

RECORD = "GOH_LAND_VERDICTS_RECORD"
REUSE = "GOH_LAND_VERDICTS_REUSE"
BIN_MARK = "goh_bin"  # a user property: the test ran the native binary
REPO_ROOT = Path(__file__).resolve().parent.parent

_state: dict[str, object] = {}
_bin: set[str] = set()
_failed: set[str] = set()
_called: set[str] = set()


def identity(root: Path = REPO_ROOT) -> str | None:
    sys.path.insert(0, str(root / "lib"))
    try:
        import gate_identity
    finally:
        sys.path.pop(0)
    return gate_identity.suite(root)


def _controller(config) -> bool:
    return not hasattr(config, "workerinput")


def mark_binary(node) -> None:
    """The test `node` ran (or will run) the native binary: its run is coverage too."""
    if (BIN_MARK, True) not in node.user_properties:
        node.user_properties.append((BIN_MARK, True))


def pytest_sessionstart(session) -> None:
    if os.environ.get(RECORD) and _controller(session.config):
        _state["identity"] = identity()


def pytest_runtest_logreport(report) -> None:
    if not os.environ.get(RECORD):
        return
    if (BIN_MARK, True) in report.user_properties:
        _bin.add(report.nodeid)
    if report.failed:
        _failed.add(report.nodeid)
    elif report.when == "call" and report.passed:
        _called.add(report.nodeid)


def pytest_sessionfinish(session) -> None:
    path = os.environ.get(RECORD)
    if not path or not _controller(session.config):
        return
    passed = {n: n in _bin for n in sorted(_called - _failed)}
    tmp = Path(f"{path}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"identity": _state.get("identity"), "passed": passed}))
    tmp.replace(path)  # atomic: a reader sees a whole record or the old one


def reusable(path: str, here: str | None, coverage: bool) -> tuple[set[str], str]:
    """(the node ids a record at `path` lets this run skip, why). `here` is this checkout's
    identity; under `coverage` a test that ran the native binary is never skipped."""
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return set(), f"no record ({e.__class__.__name__})"
    if not isinstance(rec, dict) or not isinstance(rec.get("passed"), dict):
        return set(), "a malformed record"
    if here is None or rec.get("identity") != here:
        why = "this checkout has no identity (a change to its tree)" if here is None else ""
        return set(), why or "the record is of another tree or toolchain"
    nodes = {n for n, ran_bin in rec["passed"].items() if not (coverage and ran_bin)}
    kept = len(rec["passed"]) - len(nodes)
    return nodes, f"same tree{f', {kept} that ran the binary rerun for coverage' if kept else ''}"


def instrumented() -> bool:
    """This run is the coverage step's INSTRUMENTED one: it names the binary directory AND a profile
    destination (conftest's profile carry reads the same pair). The plain run of a reused Rust
    measurement names its plain build alone (GOH.RUSTCOVSKIP)."""
    return all(os.environ.get(k) for k in ("GOH_COVERAGE_BIN_DIR", "LLVM_PROFILE_FILE"))


def _reusable(config) -> tuple[set[str], str]:
    if "reuse" not in _state:
        path = os.environ.get(REUSE, "")
        coverage = instrumented()
        _state["reuse"] = reusable(path, identity(), coverage) if path else (set(), "")
    return _state["reuse"]  # type: ignore[return-value]


def pytest_report_header(config) -> str | None:
    if not os.environ.get(REUSE):
        return None
    nodes, why = _reusable(config)
    return f"land verdicts: {len(nodes)} tests passed by the pre-land run are reused ({why})"


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items) -> None:
    if not os.environ.get(REUSE):
        return
    nodes, _ = _reusable(config)
    if not nodes:
        return
    keep, drop = [], []
    for item in items:
        (drop if item.nodeid in nodes else keep).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
