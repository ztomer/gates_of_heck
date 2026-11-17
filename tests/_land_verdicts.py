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
change to its build inputs (lib/gate_identity.py BUILD). A carried node is recorded as passed on
the new tree, so the next land carries it on; only a run that carried nothing from another tree
and failed nothing is FULL, and the release kit refuses a tip without a full record
(GOH_SHIP_FULL_SUITE, `reuse.py full-suite`). Each source is one use of the audited path: one use
in ten is a SHADOW -- its carried nodes run anyway, and a failure among them is a disagreement,
re-run once in a child pytest before it turns the path off (lib/reuse_audit.py). The controller
decides once and hands its workers the node list (xdist `workerinput`), so every worker deselects
alike and the audit counts one use per source.

Both keys are set by tools/land.sh for one land and are verdict-free (gates/verdict_free_keys.txt):
a path to one land's record is bookkeeping, and the proof a gate records is the same with or
without it, because a reused verdict is a verdict on the same tree. A hermetic child (a fixture
suite) drops every GOH_ key, so no nested suite reuses anything.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

RECORD = "GOH_LAND_VERDICTS_RECORD"
REUSE = "GOH_LAND_VERDICTS_REUSE"
BIN_MARK = "goh_bin"  # a user property: the test ran the native binary
REPO_ROOT = Path(__file__).resolve().parent.parent
CARRY = "carry"  # the audited reuse path (lib/reuse.py)
WORKER_KEY = "goh_land_reuse"  # the controller's decision, handed to each xdist worker
ANCESTORS = 20  # first-parent commits of HEAD searched for a recorded tree
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


def _store() -> str:
    """The store this run records into and carries from: GOH_REUSE_DIR, else the caller's that
    suite_home resolved before it replaced HOME; "" (no store: nothing recorded or carried) in a
    fixture session that names neither."""
    return os.environ.get("GOH_REUSE_DIR") or os.environ.get(STORE, "")


def _reuse():
    """lib/reuse.py, its root pinned to `_store()`."""
    os.environ["GOH_REUSE_DIR"] = _store()
    return _lib("reuse")


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


def _git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    return r.stdout.strip() if r.returncode == 0 else ""


def trees(root: Path, pre_sha: str) -> list[tuple[str, str]]:
    """(commit, tree) to carry from, nearest first: the pre-step's commit, then HEAD's first-parent
    ancestors (HEAD's own tree is the pre-step's when nothing rebased)."""
    shas = [pre_sha] if pre_sha else []
    shas += _git(root, "rev-list", "--first-parent", f"--max-count={ANCESTORS}", "HEAD~1").split()
    out = []
    for sha in shas:
        tree = _git(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{tree}}")
        if tree:
            out.append((sha, tree))
    return out


def diff(root: Path, sha: str) -> list[str] | None:
    """The paths whose bytes differ between sha's tree and HEAD's; None when git cannot say."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    r = subprocess.run(["git", "-C", str(root), "diff", "--name-only", "--no-renames", sha, "HEAD"],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
                       check=False)  # fmt: skip
    return [ln for ln in r.stdout.splitlines() if ln] if r.returncode == 0 else None


def choose(root: Path, changed: list[str]) -> tuple[set[str] | None, str]:
    """(what tools/affected_tests.py runs for `changed` on root: files and node ids, why) -- None
    when the change is suite-wide, so nothing may be carried across it."""
    sys.path.insert(0, str(root / "tools"))
    try:
        import affected_tests
    finally:
        sys.path.pop(0)
    wide = [p for p in changed if p in SUITE_WIDE or Path(p).name == "conftest.py"]
    base = affected_tests.Closure(root).base()
    wide += [p for p in changed if p in base]
    if wide:
        return None, f"{wide[0]} is loaded before every test"
    return set(affected_tests.affected(root, changed)), ""


def _builds(changed: list[str]) -> bool:
    """A change to the native binary's build inputs (lib/gate_identity.py BUILD)."""
    build = _lib("gate_identity").BUILD
    return any(p == b or p.startswith(f"{b}/") for p in changed for b in build)


def carried(passed: dict[str, bool], chosen: set[str], coverage: bool, binary: bool) -> set[str]:
    """The nodes of a record of another tree this run may skip: not in a file the diff chooses,
    not a node it chooses (a parametrized node by its function), not one that ran the native
    binary under coverage or across a change to the binary's build (`binary`)."""
    files = {c for c in chosen if "::" not in c}
    out = set()
    for node, ran_bin in passed.items():
        if ran_bin and (coverage or binary):
            continue
        if node.split("::")[0] in files or node.split("[")[0] in chosen:
            continue
        out.add(node)
    return out


def _controller(config) -> bool:
    return not hasattr(config, "workerinput")


def mark_binary(node) -> None:
    """The test `node` ran (or will run) the native binary: its run is coverage too."""
    if (BIN_MARK, True) not in node.user_properties:
        node.user_properties.append((BIN_MARK, True))


def pytest_sessionstart(session) -> None:
    if os.environ.get(RECORD) and _controller(session.config):
        _state["identity"] = identity()
        _state["sha"] = _git(REPO_ROOT, "rev-parse", "HEAD")
    _say(session)


def pytest_runtest_logreport(report) -> None:
    if not (os.environ.get(RECORD) or os.environ.get(REUSE)):
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
    path = os.environ.get(RECORD)
    if path and path != os.devnull:  # /dev/null: the store's record alone (a full run by hand)
        passed = {n: n in _bin for n in sorted(_called - _failed)}
        tmp = Path(f"{path}.{os.getpid()}.tmp")
        rec = {"identity": _state.get("identity"), "sha": _state.get("sha"), "passed": passed}
        tmp.write_text(json.dumps(rec))
        tmp.replace(path)  # atomic: a reader sees a whole record or the old one
    if path or os.environ.get(REUSE):
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
    for n, ran_bin in plan.get("skipped", {}).items():  # passed here: same tree, or carried
        if (rootdir / n.split("::")[0]).is_file():
            passed.setdefault(n, ran_bin)
    other = int(plan.get("other", 0))
    full = _whole(session.config) and session.exitstatus == 0 and not _failed and not other
    by = "land gate" if os.environ.get(REUSE) else "pre-land"
    _reuse().record_suite(REPO_ROOT, me[0], me[1], passed, set(_failed), full, by, other)


def _shadows() -> None:
    """Each sampled source: its carried nodes ran; a failure among them is a disagreement."""
    plan = _state.get("plan") or {}
    if plan.get("shadows"):
        _reuse()
    audit = _lib("reuse_audit")
    for sh in plan.get("shadows", []):
        nodes = set(sh["nodes"])
        if not nodes & (_called | _failed):
            continue  # none ran (an interrupted run): nothing to compare
        bad = sorted(nodes & _failed)
        fresh = "red" if bad else "green"
        audit.compare(CARRY, sh["fields"], "green", fresh, lambda sh=sh, bad=bad: _again(sh, bad))


def _again(sh: dict, bad: list[str]) -> tuple[str, str]:
    """Both sides again: the record read again, the failed carried nodes run again in a child."""
    reuse = _reuse()
    f = sh["fields"]
    rec, _ = reuse.lookup(CARRY, f)
    reused = "green" if rec and all(n in rec.get("passed", {}) for n in bad) else ""
    env = {k: v for k, v in os.environ.items() if k not in (RECORD, REUSE)}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *bad],
                       cwd=sh["rootdir"], env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    return reused, "green" if r.returncode == 0 else "red"


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


def _plan(config) -> dict:
    """What this run skips, decided ONCE, by the controller; an xdist worker reads its decision."""
    if "plan" in _state:
        return _state["plan"]  # type: ignore[return-value]
    if not _controller(config):
        raw = config.workerinput.get(WORKER_KEY)
        _state["plan"] = {"skip": set(json.loads(raw)) if raw else set(), "lines": []}
        return _state["plan"]  # type: ignore[return-value]
    path = os.environ.get(REUSE, "")
    coverage = instrumented()
    same, why = reusable(path, identity(), coverage) if path else (set(), "")
    lines = [f"land verdicts: {len(same)} tests passed by the pre-land run are reused ({why})"]
    skipped = {n: b for n, b in _file_passed(path).items() if n in same}
    plan = {"lines": lines, "skipped": skipped, "other": 0, "shadows": []}
    if path:
        _carry(plan, _file_sha(path), coverage, Path(config.rootpath))
    plan["skip"] = set(plan["skipped"])
    _state["plan"] = plan
    return plan


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
    sha = rec.get("sha") if isinstance(rec, dict) else ""
    return sha if isinstance(sha, str) else ""


def _carry(plan: dict, pre_sha: str, coverage: bool, rootdir: Path) -> None:
    """Add to `plan` the nodes carried from the recorded trees: each source, one audited use."""
    lines: list[str] = plan["lines"]
    me = _here()
    if me is None or not _store():
        why = "this checkout has no identity" if _store() else "no verdict store is named"
        lines.append(f"land verdicts: nothing carried ({why})")
        return
    tree, tools = me
    reuse, audit = _reuse(), _lib("reuse_audit")
    taken = 0
    for sha, src in trees(REPO_ROOT, pre_sha):
        if taken >= SOURCES:
            break
        rec, _ = reuse.lookup_suite(REPO_ROOT, src, tools)
        if rec is None:
            continue
        taken += 1
        passed = rec["passed"]
        if src == tree:
            nodes, how = {n for n, b in passed.items() if not (coverage and b)}, "the same tree"
        else:
            changed = diff(REPO_ROOT, sha)
            chosen, why = choose(REPO_ROOT, changed) if changed is not None else (None, "no diff")
            if chosen is None:
                lines.append(f"land verdicts: nothing carried from {sha[:8]}: {why}")
                continue
            nodes = carried(passed, chosen, coverage, _builds(changed or []))
            how = f"{len(changed or [])} paths differ, affected_tests chooses {len(chosen)}"
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
        if src != tree:
            plan["other"] += len(nodes)
        lines.append(f"land verdicts: {len(nodes)} deselected, carried from {sha[:8]} ({how})")
    for sh in plan["shadows"]:  # a node another source skips does not run: not this shadow's
        sh["nodes"] = [n for n in sh["nodes"] if n not in plan["skipped"]]


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node) -> None:
    """xdist, in the controller: hand each worker the one decision."""
    if os.environ.get(REUSE):
        node.workerinput[WORKER_KEY] = json.dumps(sorted(_plan(node.config)["skip"]))


def _say(session) -> None:
    """What the gate skips, said even under -q (a report header is not): one line per source."""
    if not (os.environ.get(REUSE) and _controller(session.config)):
        return
    term = session.config.pluginmanager.get_plugin("terminalreporter")
    for line in _plan(session.config)["lines"]:
        (term.write_line if term else print)(line)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items) -> None:
    if not os.environ.get(REUSE):
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
