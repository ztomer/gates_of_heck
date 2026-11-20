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

COVERAGE. Inside the coverage step goh's suite drives the INSTRUMENTED binary (GOH_COVERAGE_BIN_DIR
and LLVM_PROFILE_FILE set), so a test that ran it is coverage the floor counts: it is skipped only
when its stored profile of THIS binary is put where the report reads it (`carried`, GOH.COVCARRY3).

CARRIED ACROSS TREES (GOH.VERDICTCARRY). With three lanes landing, main moved under 5 of 8 green
pre-steps (2026-11-16), so a land's gate re-ran the whole suite (551-731 s at load). A verdict is
keyed by what decides it, and a test's inputs are narrower than the tree (rule 15): every run that
records (either key above) also merges its passes into the one store's `carry` path (lib/reuse.py:
the repo, the TREE it judged, the tools), and the gate's run deselects a node that passed on another
tree T when the diff T..HEAD does not choose it -- tools/affected_tests.py, the selection the
pre-step already trusts, asked on HEAD's tree. Tree readers always run (affected chooses them on
every diff). Its sources: the pre-step's tree (the record names its commit) and the nearest
records on HEAD's first-parent chain (main's tip, which the last land's gate recorded). It carries
nothing from a tree whose diff touches what pytest loads before every test (affected_tests'
Closure.base, pyproject.toml, tools/pytest.sh), and no node that ran the native binary across a
change to its build inputs (binary_runners.build_input). A carried node is recorded as passed on
the new tree, so the next land carries it on, and stays CARRIED there: a later run taking it
from that tree's own record counts it carried, not judged (GOH.CARRYASFULL); only a run that
carried nothing and failed nothing is FULL, and the release kit refuses a tip without a full record
(GOH_SHIP_FULL_SUITE, `reuse.py full-suite`). Each source is one use of the audited path: one use
in ten is a SHADOW -- its carried nodes run anyway, and a failure among them is a disagreement,
re-run once in a child pytest before it turns the path off (lib/reuse_audit.py). The controller
decides once and hands its workers the node list (xdist `workerinput`), so every worker deselects
alike and the audit counts one use per source.

Both keys are set by tools/land.sh for one land and are verdict-free (gates/verdict_free_keys.txt:
a reused verdict is a verdict on the same tree). A hermetic child drops every GOH_ key, and the
plugin takes the keys OUT of the environment as it loads (`_take`), so no session a test starts
reuses or records (GOH.VERDICTCARRY3, tests/test_verdict_carry_hermetic.py).
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path

import _cov_claims  # tests/: the non-binary carry and its gate (GOH.COVNONBIN)
import _land_audit  # tests/, beside this file: the audited `test` and `carry` uses
import _prof_record  # tests/: each test's own profile under coverage (GOH.COVCARRY)
import _same_tree  # tests/: the fresh instrumented run's own-tree reuse (@tree)
import pytest
from _land_trees import ANCESTORS, _git, carried, diff, trees  # noqa: F401  (patched here)

RECORD = "GOH_LAND_VERDICTS_RECORD"
REUSE = "GOH_LAND_VERDICTS_REUSE"
BIN_MARK = "goh_bin"  # a user property: the test ran the native binary
REPO_ROOT = Path(__file__).resolve().parent.parent
CARRY = "carry"  # the audited reuse path (lib/reuse.py); a same-tree node's, `test`: _land_audit
DROPPED_KEY = "goh_land_verdicts_dropped"  # worker -> controller: nodes it deselected
WORKER_KEY = "goh_land_reuse"  # the controller's decision, handed to each xdist worker
SOURCES = 2  # ... of which this many records are carried from (each costs one affected_tests run)
SUITE_WIDE = ("pyproject.toml", "tools/pytest.sh", "gates/_pytest_root.sh")

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


STORE = "GOH_LAND_VERDICTS_STORE"  # the caller's store root (tests/suite_home.py `_land_store`)


def _take() -> dict[str, str]:
    """This session's land keys, taken OUT of the environment when the plugin loads.

    Left in it, every pytest session a test started with the suite's environment (a probe of the
    session start, test_temp_claimable's; test_hook_git_env's) inherited them: it recorded 1-pass
    'pre-land' and 'land gate' records for the REAL tree into the REAL store, where a carry could
    trust them, overwrote the land's record file, and deselected its own node by the land's record
    (GOH.VERDICTCARRY3). A session a test starts now sees only the keys that test names. The store
    is GOH_REUSE_DIR when one is named, else the caller's that suite_home resolved."""
    got = {k: os.environ.pop(k) for k in (RECORD, REUSE) if k in os.environ}
    store = os.environ.get("GOH_REUSE_DIR") or os.environ.pop(STORE, "")
    os.environ.pop(STORE, None)
    if store:
        got[STORE] = store
    return got


_KEYS = _take()


def _on(name: str) -> str:
    return _KEYS.get(name, "")


def _store() -> str:
    """The store this run records into and carries from; "" in a fixture session naming none."""
    return _on(STORE)


@contextlib.contextmanager
def _in_store():
    """lib/reuse.py and lib/reuse_audit.py, their root pinned to `_store()` for the call only: set
    for good, a test's child inherited the land's store (GOH.VERDICTCARRY3)."""
    was = os.environ.get("GOH_REUSE_DIR")
    os.environ["GOH_REUSE_DIR"] = _store()
    try:
        yield _lib("reuse"), _lib("reuse_audit")
    finally:
        if was is None:
            os.environ.pop("GOH_REUSE_DIR", None)
        else:
            os.environ["GOH_REUSE_DIR"] = was


def _store_or_none():  # `_in_store`, when a store is named to count in; else None
    return _in_store if _store() else None


def _lib(name: str):
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    try:
        return __import__(name)
    finally:
        sys.path.pop(0)


def here(root: Path = REPO_ROOT) -> tuple[str, str] | None:
    """(tree, tools) of this checkout when it is clean (lib/gate_identity.py)."""
    gi = _lib("gate_identity")
    tree = gi.clean_tree(root)
    return None if tree is None else (tree, gi.suite_tools())


def choose(root: Path, changed: list[str], table=None) -> tuple[set[str] | None, str]:
    """(what tools/affected_tests.py -- or `table`, one loaded apart -- runs for `changed` on root:
    files and node ids, why); None when the change is suite-wide, so nothing carries across it."""
    if table is None:
        sys.path.insert(0, str(root / "tools"))
        try:
            import affected_tests as table
        finally:
            sys.path.pop(0)
    wide = [p for p in changed if p in SUITE_WIDE or Path(p).name == "conftest.py"]
    wide += [p for base in [table.Closure(root).base()] for p in changed if p in base]
    if wide:
        return None, f"{wide[0]} is loaded before every test"
    return set(table.affected(root, changed)), ""


def _builds(changed: list[str], root: Path = REPO_ROOT) -> bool:
    """A change to the native binary's build inputs at `root`: the four roots and each file a
    crate embeds (lib/binary_runners.py build_input, GOH.BUILDINCLUDES)."""
    return _lib("binary_runners").build_input(root, changed) is not None


def pytest_configure(config) -> None:
    """The measurement this plugin's coverage rule reads, registered beside it (GOH.COVCARRY)."""
    if not config.pluginmanager.has_plugin("goh-prof-record"):
        config.pluginmanager.register(_prof_record, "goh-prof-record")


def _controller(config) -> bool:
    return not hasattr(config, "workerinput")


def mark_binary(node) -> None:
    """The test `node` ran (or will run) the native binary: its run is coverage too."""
    if (BIN_MARK, True) not in node.user_properties:
        node.user_properties.append((BIN_MARK, True))


def pytest_sessionstart(session) -> None:
    if _on(RECORD) and _controller(session.config):
        _state["identity"] = identity()
        _state["sha"] = _git(REPO_ROOT, "rev-parse", "HEAD")
    _say(session)


def pytest_runtest_logreport(report) -> None:
    if not (_on(RECORD) or _on(REUSE)):  # the controller's keys: it judges every report
        return
    if (BIN_MARK, True) in report.user_properties:
        _bin.add(report.nodeid)
    if report.failed:
        _failed.add(report.nodeid)
    elif report.when == "call" and report.passed:
        _called.add(report.nodeid)


def _here() -> tuple[str, str] | None:
    if "here" not in _state:
        _state["here"] = here()
    return _state["here"]  # type: ignore[return-value]


def _whole(config) -> bool:
    """This run is the whole suite: no node, -k, -m, --deselect or --lf narrowed it."""
    o = config.option
    if any(getattr(o, k, None) for k in ("keyword", "markexpr", "deselect", "lf")):
        return False
    roots = {Path(config.rootpath), Path(config.rootpath) / "tests"}
    return all("::" not in a and Path(a).resolve() in roots for a in config.args)


def pytest_sessionfinish(session) -> None:
    if not _controller(session.config):
        return
    # Every node reused or carried is a green run, not "no tests ran" (GOH.CARRYALL: the v0.130.0
    # release gate deselected all 5812 and pytest's exit 5 failed the land). An empty collection
    # with nothing deselected stays 5. Mapped before the store reads exitstatus: a whole run
    # records `full`, so the next tree can carry from it.
    if (
        session.exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED
        and _state.get("dropped")
        and not _failed
    ):
        session.exitstatus = pytest.ExitCode.OK
    path = _on(RECORD)
    if path and path != os.devnull:  # /dev/null: the store's record alone (a full run by hand)
        passed = {n: n in _bin for n in sorted(_called - _failed)}
        tmp, was = Path(f"{path}.{os.getpid()}.tmp"), _state.get("identity")
        # Its identity is the tree the run STARTED and ENDED on (GOH.SUITEONCE, GOH.LANDSNAPSHOT).
        rec = {"identity": was if was == identity() else None, "sha": _state.get("sha"),
               "passed": passed}  # fmt: skip
        tmp.write_text(json.dumps(rec))
        tmp.replace(path)  # atomic: a reader sees a whole record or the old one
    if path or _on(REUSE):
        _shadows()
        _record_store(session)


def _record_store(session) -> None:
    """Merge this run's verdicts, and the ones it skipped as passed, into the tree's record."""
    me = _here()
    if me is None or not _store():
        return
    plan = _state.get("plan") or {}
    rootdir = Path(session.config.rootpath)
    passed = {n: n in _bin for n in _called - _failed}
    # What a run saw each node do (profiles; a plain run's marks, tests/_prof_record.py): carriable.
    measured = dict(passed) if instrumented() else _prof_record.measured(passed)
    for n, ran_bin in plan.get("skipped", {}).items():  # passed here: same tree, or carried
        if (rootdir / n.split("::")[0]).is_file():
            passed.setdefault(n, ran_bin)
            if n in plan.get("binprof", set()):
                measured.setdefault(n, True)
    other = int(plan.get("other", 0))
    judged = (_called - _failed) | (set(plan.get("judged", ())) - _failed)
    with _in_store() as (reuse, _):  # the measurement carried, its claims checked (GOH.COVNONBIN)
        _cov_claims.record(session, plan, reuse.lookup_suite(REPO_ROOT, *me)[0], passed, measured,
                           _bin, instrumented())  # fmt: skip
        full = _whole(session.config) and session.exitstatus == 0 and not _failed and not other
        by = _same_tree.by(_on(REUSE), _on(RECORD), _whole(session.config))  # what ran
        reuse.record_suite(REPO_ROOT, me[0], me[1], passed, set(_failed), full, by, other,
                           instrumented(), measured, judged)  # fmt: skip


def _shadows() -> None:
    """Each sampled source's carried nodes, each sampled same-tree node, ran: a failure among
    them is a disagreement, re-run in a child pytest first (tests/_land_audit.py)."""
    _land_audit.shadows(
        _state.get("plan") or {},
        _in_store,
        _called,
        _failed,
        (RECORD, REUSE, STORE, "GOH_REUSE_DIR"),
        lambda n: n in reusable(_on(REUSE), identity(), instrumented())[0],
    )


def reusable(path: str, here: str | None, coverage: bool) -> tuple[set[str], str]:
    """(the node ids a record at `path` lets this run skip, why). `here` is this checkout's
    identity; under `coverage` a test that ran the native binary is never skipped."""
    if path == _same_tree.SAME:  # this tree's store record, read again (the audit's re-read)
        return _same_tree.nodes(_own_record()), "@tree"
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


def _plan(config) -> dict:
    """What this run skips, decided ONCE, by the controller; an xdist worker reads its decision."""
    if "plan" in _state:
        return _state["plan"]  # type: ignore[return-value]
    if not _controller(config):
        raw = config.workerinput.get(WORKER_KEY)
        _state["plan"] = {"skip": set(json.loads(raw)) if raw else set(), "lines": []}
        return _state["plan"]  # type: ignore[return-value]
    path = _on(REUSE)
    coverage = instrumented()
    if path and coverage and not _carry_audited_on():  # GOH.COVCARRY2: the kill switch
        lines = ["land verdicts: nothing reused under coverage: the audit turned `profcarry` off"]
        _state["plan"] = {"lines": lines, "skipped": {}, "other": 0, "shadows": [], "skip": set()}
        return _state["plan"]  # type: ignore[return-value]
    if path == _same_tree.SAME:  # the ship's fresh run: no file, no carry, no stored profile
        _state["plan"] = _same_tree.plan(_own_record(), coverage, lambda p, n: _land_audit.audited(
            p, n, identity(), str(config.rootpath), _store_or_none()))  # fmt: skip
        return _state["plan"]  # type: ignore[return-value]
    same, why = reusable(path, identity(), coverage) if path else (set(), "")
    lines = [f"land verdicts: {len(same)} tests passed by the pre-land run are reused ({why})"]
    plan = {"lines": lines, "skipped": {}, "other": 0, "shadows": []}
    if same:  # each node one use of the audited `test` path, decided here for every worker
        same = _land_audit.audited(plan, same, identity(), str(config.rootpath), _store_or_none())
    plan["skipped"] = {n: b for n, b in _file_passed(path).items() if n in same}
    plan["judged"] = set(plan["skipped"])  # the pre-step's passes of THIS tree (its identity)
    if path:
        _carry(plan, _file_sha(path), coverage, Path(config.rootpath))
    if coverage:  # a same-tree pass is skipped only when it was measured running no binary
        nobin = plan.get("nobin", set())
        dropped = {n for n in same if n not in nobin}
        plan["skipped"] = {n: b for n, b in plan["skipped"].items() if n not in dropped}
        if dropped:
            lines.append(f"land verdicts: {len(dropped)} same-tree passes rerun under coverage: "
                         "no instrumented run measured them running no binary")  # fmt: skip
    plan["skip"] = set(plan["skipped"])
    _state["plan"] = plan
    return plan


def _own_record() -> dict | None:
    """This tree's suite record in the store, or None (no identity, no store)."""
    if (me := _here()) is None or not _store():
        return None
    with _in_store() as (reuse, _):
        return reuse.lookup_suite(REPO_ROOT, *me)[0]


def _carry_audited_on() -> bool:
    """Off once a fresh full instrumented run disagreed with a land's claim (lib/prof_carry.py)."""
    if not _store():
        return True
    with _in_store():
        return _lib("prof_carry").on()


def _file_passed(path: str) -> dict[str, bool]:
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    passed = rec.get("passed") if isinstance(rec, dict) else None
    return passed if isinstance(passed, dict) else {}


def _file_sha(path: str) -> str:
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return rec["sha"] if isinstance(rec, dict) and isinstance(rec.get("sha"), str) else ""


def _carry(plan: dict, pre_sha: str, coverage: bool, rootdir: Path) -> None:
    """Add to `plan` the nodes carried from the recorded trees: each source, one audited use."""
    lines: list[str] = plan["lines"]
    me = _here()
    if me is None or not _store():
        why = "this checkout has no identity" if _store() else "no verdict store is named"
        lines.append(f"land verdicts: nothing carried ({why})")
        return
    tree, tools = me
    with _in_store() as (reuse, audit):
        taken = 0
        for sha, src in trees(REPO_ROOT, pre_sha):
            if taken >= SOURCES:
                break
            rec, _ = reuse.lookup_suite(REPO_ROOT, src, tools)
            if rec is None:
                continue
            taken += 1
            passed = rec["passed"]
            measured = rec.get("measured") if isinstance(rec.get("measured"), dict) else {}
            held = _prof_record.held(src, tools, {n for n, b in passed.items() if b})
            if src == tree:
                nodes = {n for n, b in passed.items() if not coverage or n in held
                         or (not b and measured.get(n) is False)}  # fmt: skip
                plan.setdefault("nobin", set()).update(n for n, b in measured.items() if b is False)
                how = "the same tree"
            else:
                changed = diff(REPO_ROOT, sha)
                chosen, why = (
                    choose(REPO_ROOT, changed) if changed is not None else (None, "no diff")
                )
                if chosen is None:
                    lines.append(f"land verdicts: nothing carried from {sha[:8]}: {why}")
                    continue
                nodes = carried(passed, chosen, coverage, _builds(changed or []), measured, held)
                plan.setdefault("nobin", set()).update(
                    carried(measured, chosen, True, False, measured)
                )
                how = f"{len(changed or [])} paths differ, affected_tests chooses {len(chosen)}"
            if coverage and (src == tree or not _builds(changed or [])):  # GOH.PROFKEYMISS
                plan.setdefault("profowed", []).append((src, len(held)))  # owed a match, matched
            nodes -= set(plan["skipped"])
            if not nodes:
                continue
            fields = reuse.carry_fields(REPO_ROOT, src, tools)
            decision = audit.use("carry", fields)
            if decision == "off":
                lines.append("land verdicts: nothing carried: the audit turned `carry` off "
                             "(lib/reuse_audit.py report)")  # fmt: skip
                return
            if decision == "shadow":
                plan["shadows"].append({"fields": fields, "nodes": sorted(nodes), "sha": sha,
                                        "rootdir": str(rootdir)})  # fmt: skip
                lines.append(f"land verdicts: {len(nodes)} carried from {sha[:8]} RUN anyway: the "
                             f"audit's shadow ({how})")  # fmt: skip
                continue
            plan["skipped"].update((n, bool(passed[n])) for n in nodes)
            if coverage and nodes & held:  # their profiles, where the report reads them
                _prof_record.take(src, tools, nodes & held)
                plan.setdefault("binprof", set()).update(nodes & held)
                how += f", {len(nodes & held)} by their stored profiles"
            # A pass is judged here only when the record says a run made it ON this tree: one an
            # earlier run carried onto it is carried still (GOH.CARRYASFULL).
            mine = nodes & set(rec.get("judged") or ()) if src == tree else set()
            plan.setdefault("judged", set()).update(mine)
            plan["other"] += len(nodes - mine)
            lines.append(f"land verdicts: {len(nodes)} deselected, carried from {sha[:8]} ({how})")
    for sh in plan["shadows"]:  # a node another source skips does not run: not this shadow's
        sh["nodes"] = [n for n in sh["nodes"] if n not in plan["skipped"]]


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node) -> None:
    """xdist, in the controller: hand each worker the one decision."""
    if _on(REUSE):
        node.workerinput[WORKER_KEY] = json.dumps(sorted(_plan(node.config)["skip"]))


def _say(session) -> None:
    """What the gate skips, said even under -q (a report header is not): one line per source."""
    if not (_on(REUSE) and _controller(session.config)):
        return
    term = session.config.pluginmanager.get_plugin("terminalreporter")
    for line in _plan(session.config)["lines"]:
        (term.write_line if term else print)(line)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items) -> None:
    # A worker learns the controller's decision from its workerinput: the keys left the
    # environment before any worker started (`_take`).
    if not (_on(REUSE) if _controller(config) else WORKER_KEY in config.workerinput):
        return
    nodes = _plan(config)["skip"]
    if not nodes:
        return
    keep, drop = [], []
    for item in items:
        (drop if item.nodeid in nodes else keep).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
        if _controller(config):
            _state["dropped"] = len(drop)
        else:  # under xdist the controller collects nothing: the worker tells it
            config.workeroutput[DROPPED_KEY] = len(drop)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error) -> None:
    """xdist, in the controller: how many collected nodes a worker deselected as passed."""
    n = getattr(node, "workeroutput", {}).get(DROPPED_KEY, 0)
    _state["dropped"] = max(int(_state.get("dropped", 0)), n)  # each worker collects the whole set
