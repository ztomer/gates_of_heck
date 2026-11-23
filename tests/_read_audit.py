"""What each test file READ of the tree while it ran, so a carry can refuse a read no rule declares.

GOH.READKEYVERDICT. A pass is carried to another tree when tools/affected_tests.py, asked about the
diff between them, does not choose it (tests/_land_verdicts.py): the selector's rules -- what a test
names, imports, reaches, globs or declares (`TREE_READS`, `JUDGE_READS`, lib/declared_reads.py) --
are its read set, and an unchanged read set keeps the verdict. A test that opens a file none of
those rules sees has a read set the carry cannot know, and a change to that file would be carried
past green. So every run that records (the land's keys) watches what its tests open:

  * an audit hook (sys.addaudithook: `open`, `os.listdir`, `os.scandir`) notes each path under the
    run's root a test opens or lists between its setup and its teardown, in the process the test
    runs in (an xdist worker is told to by its controller, `WORKER_KEY`);
  * the paths ride the teardown report to the controller (`READS_PROP`), keyed by test FILE: a
    module fixture's read belongs to the file, whichever of its tests started it;
  * the record of the run's tree keeps them (`reads`, lib/reuse.py record_suite), and a carry from
    it refuses each node whose file read a path the diff changed though the selector did not
    choose it (`refused`): it runs, and the line names the test and the path.

More than SPAN paths of one file are kept as their directories (two levels): coarser, so a carry
refuses more, never less. What it cannot see: a read made by a CHILD process (a gate script a test
runs; its own reads are the selector's closure of that script), and one a SESSION fixture made
before the first test (the clone templates, tests/_tree_clone.py, built from declared copies).
Pinned by tests/test_read_audit.py.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

READS_PROP = "goh_reads"  # a teardown report's user property: the paths its file's test read
WORKER_KEY = "goh_read_audit"  # the controller -> worker word: audit this worker's tests
SPAN = 64  # more paths than this of one file are kept as their directories
SKIP = (".git/", "target/", ".pytest_cache/")

_state: dict[str, object] = {"on": False, "roots": (), "current": None, "hooked": False}
_reads: dict[str, set[str]] = {}  # the controller's: test file -> what its tests read


def _note(event: str, args: tuple) -> None:
    """The audit hook: a path opened or listed under the root while a test runs."""
    current = _state["current"]
    if current is None or event not in ("open", "os.listdir", "os.scandir") or not args:
        return
    try:
        raw = args[0]
        if raw is None or isinstance(raw, int):
            return
        given = os.fsdecode(raw)
        path = os.path.abspath(given)
        # A relative open may be relative to a directory fd the event does not name (shutil.rmtree
        # opens each directory it removes by name, dir_fd=its parent): only a file that IS there,
        # from the cwd, is read from the cwd.
        if event == "open" and not os.path.isabs(given) and not os.path.isfile(path):
            return
        roots: tuple[str, ...] = _state["roots"]  # type: ignore[assignment]
        root = next((r for r in roots if path.startswith(r + os.sep)), None)
        if root is None:
            return
        rel = path[len(root) + 1 :].replace(os.sep, "/")
        if "__pycache__" in rel or rel.startswith(SKIP) or rel.endswith((".pyc", ".pyo")):
            return
        listed = event != "open" or (len(args) > 2 and isinstance(args[2], int)
                                     and args[2] & getattr(os, "O_DIRECTORY", 0))  # fmt: skip
        current.add(rel.rstrip("/") + "/" if listed else rel)  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 -- the audit never fails a test
        return


def start(root: str) -> None:
    """Watch this process's tests (once a process: an audit hook cannot be removed)."""
    _state["on"] = True
    _state["roots"] = tuple(dict.fromkeys((os.path.abspath(root), os.path.realpath(root))))
    if not _state["hooked"]:
        sys.addaudithook(_note)
        _state["hooked"] = True


def kept(paths: set[str]) -> list[str]:
    """PATHS as stored: each path, or past SPAN their directories, two levels deep."""
    if len(paths) <= SPAN:
        return sorted(paths)
    dirs = set()
    for p in paths:
        parts = p.rstrip("/").split("/")
        dirs.add("/".join(parts[: min(2, len(parts) - 1)]) + "/" if len(parts) > 1 else p)
    return sorted(dirs)


def hits(read: str, path: str) -> bool:
    """Whether a kept READ covers PATH: the file itself, or a directory above it."""
    return path == read or (read.endswith("/") and path.startswith(read))


def refused(nodes: set[str], reads: dict[str, list[str]],
            changed: list[str]) -> dict[str, tuple[str, str]]:  # fmt: skip
    """{node: (its file, the changed path it read)} for each of NODES (carriable by the selector)
    whose file read a changed path, or has no reads kept at all: the record names what every
    audited file read, an empty list included, so a file missing from it was never watched."""
    out: dict[str, tuple[str, str]] = {}
    for node in nodes:
        f = node.split("::")[0]
        got = reads.get(f)
        if got is None:
            out[node] = (f, "")
            continue
        if hit := next((p for p in changed for r in got if hits(r, p)), None):
            out[node] = (f, hit)
    return out


def collected() -> dict[str, list[str]]:
    """{test file: what it read, as stored} for every file the controller heard from."""
    return {f: kept(paths) for f, paths in _reads.items()}


def merged(passed, own: dict, kept_from: dict | None = None,
           old: dict | None = None) -> dict[str, list[str]]:  # fmt: skip
    """{test file: what it read} for the files of PASSED and of OLD's passes (the tree's record
    before this run), joined per file: what this run's tests read (OWN, `collected`), what the
    source records of the nodes it skipped kept (KEPT_FROM), and OLD's. A file none of them names
    stays out: it was never watched, and no carry takes it (`refused`)."""
    old = old or {}
    files = {n.split("::")[0] for n in (*passed, *(old.get("passed") or {}))}
    out: dict[str, set[str]] = {}
    for src in (old.get("reads") or {}, kept_from or {}, own):
        for f, paths in src.items():
            if f in files:
                out.setdefault(f, set()).update(paths)
    return {f: sorted(p) for f, p in sorted(out.items())}


def ours(passed, kept_from: dict | None = None, old: dict | None = None) -> dict[str, list[str]]:
    """`merged` with this process's own watched reads (`collected`)."""
    return merged(passed, collected(), kept_from, old)


def of_record(path: str) -> dict[str, list[str]]:
    """The `reads` of the land record at PATH; {} for none."""
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    reads = rec.get("reads") if isinstance(rec, dict) else None
    return reads if isinstance(reads, dict) else {}


def keep(plan: dict, rec: dict, nodes: set[str]) -> None:
    """The reads REC kept for the files of NODES, which a carry skips, into PLAN's `reads`."""
    reads = rec.get("reads") if isinstance(rec.get("reads"), dict) else {}
    for f in {n.split("::")[0] for n in nodes} & set(reads):
        plan.setdefault("reads", {}).setdefault(f, reads[f])


def undeclared(lines: list[str], rec: dict, nodes: set[str], changed: list[str],
               sha: str) -> set[str]:  # fmt: skip
    """The NODES the selector would carry from REC whose test file read a changed path no rule of
    the selector declares, or was never watched (`refused`): they run, and a line in LINES says
    why. A record from before the audit (no `reads` at all) refuses none, for its one-day TTL."""
    reads = rec.get("reads")
    if not isinstance(reads, dict) or not nodes:
        return set()
    out = refused(nodes, reads, changed)
    if out:
        hit = next(((f, p) for f, p in sorted(out.values()) if p), None)
        why = (f"{hit[0]} read {hit[1]}, which the diff changes and no rule of the selector "
               "declares" if hit else "their files' reads were never watched")  # fmt: skip
        lines.append(f"land verdicts: {len(out)} not carried from {sha[:8]}: {why}")
    return set(out)


def pytest_configure(config) -> None:
    worker = getattr(config, "workerinput", None)
    if worker is not None and worker.get(WORKER_KEY):
        start(str(config.rootpath))


def on_controller(config) -> None:
    """The controller's run watches its own tests too (serial); tests/_land_verdicts.py: when."""
    start(str(config.rootpath))


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node) -> None:
    if _state["on"]:
        node.workerinput[WORKER_KEY] = "1"


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item) -> None:
    if _state["on"]:
        _state["current"] = set()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    if _state["on"] and call.when == "teardown" and _state["current"] is not None:
        got, _state["current"] = _state["current"], None
        item.user_properties.append((READS_PROP, sorted(got)))  # type: ignore[arg-type]
    yield


def pytest_runtest_logreport(report) -> None:
    if not _state["on"] or report.when != "teardown":
        return
    f = report.nodeid.split("::")[0]
    for name, value in report.user_properties:
        if name == READS_PROP:
            _reads.setdefault(f, set()).update(value)
            return
    _reads.setdefault(f, set())
