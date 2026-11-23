#!/usr/bin/env python3
"""The pre-land test set, chosen from the diff (GOH.TREETESTSFIRST).

    affected_tests.py [--root DIR] [--base REV] [--why]   # the test files, one per line
    affected_tests.py --root DIR --changed PATH...         # the same for a named change
    affected_tests.py --run [--base REV]                   # run them: tools/pytest.sh <the set>

WHY. A lane ran the tests it chose by hand, then lost a whole land gate (a slot wait and ~8 min of
suite) to one it had not chosen: GOH.ONEADMISSION's first land (2026-11-16, 1089 s over two
attempts) went red on tests/test_lock_order.py, which RUNS the changed tools/load.py by path
(`REPO_ROOT / "tools" / "load.py"`), and GOH.TAGPUSHGATE's on tests/test_self_walk.py, a tree reader
whose name matches no pattern. The choice was by hand and by name, so it is made here, from the
diff, and tools/land.sh runs the set (GOH_LAND_PRE in .gatesrc) before the land lock and before the
full gate's slot: a test the lane missed fails in seconds.

THE SET is the union of
  * the CHANGED test files themselves (added or modified under tests/);
  * every tests/test_*.py that NAMES a changed path -- its text holds the path, or every component
    of it as a quoted literal (`"tools" / "load.py"`, `os.path.join(ROOT, "tools", "load.py")`)
    -- or IMPORTS a changed Python module (`import admission`, `from lib import admission`, an
    import of a changed tests/ helper), read with ast;
  * the TREE READERS, chosen by what they read: a test file that defines a test_every_/test_no_
    function, or whose source lists or walks the tracked tree (`git ls-files`, `rglob(`,
    `os.walk(`, a glob or iterdir of a REPO_ROOT directory), or reaches a tests/ helper's walk OF
    THE ROOT -- importing a helper that walks only what it is handed (tests/_tree_clone.py's
    templates) is no tree read (lib/tree_walk.py, GOH.CLONENOTJUDGE) -- plus ALWAYS (readers no
    pattern sees). A changed path can break a tree reader whatever it names, so the land gate
    runs them all; the enqueue runs one whose reads are known only for a diff touching them.
  * the TRANSITIVE closure of each test (GOH.AFFECTEDTRANSITIVE): what a test imports, what that
    imports (tests/ helpers, checks/, lib/, tools/ modules: lib/source_closure.py reads the
    edges, the one dependency reader), and a script it names by path with what that script sources
    -- a test is chosen when a changed file is anywhere in that closure, or a file in it NAMES a
    changed path. A test MODULE another file imports names from is in the closure as the slice
    those names reach (lib/test_slice.py, GOH.SELECTTESTIMPORTS): a `_repo` helper of
    tests/test_push_gate.py brings what `_repo` reads, not what that file's own tests run; a
    Python tool brings the shell scripts its code runs by path (tools/landq.py: tools/land.sh).
    tests/test_gate_slot_race.py imports checks/_gate_slot_probe.py, which copies
    and patches lib/gate_slot.py by name: a change to the wait text there is the race test's,
    though the test says neither (SLOTFAIR land 1, 706 s). A naming edge (a file naming a script
    it runs) is followed from a test and the tests/ and checks/ helpers it reaches, never from a
    lib/ or tools/ module's prose, and never twice in a row: a script's own mentions are not run.
    What a reached SHELL script runs by a variable-rooted path (`"$GOH_PY" "$ROOT/lib/x.py"`) is
    an edge, a file pytest loads before every test included (GOH.PREPARITY: gates/_rust_proven.sh
    runs lib/gate_identity.py, which tests/_land_verdicts.py imports; the pre-step missed
    tests/test_rust_gate.py and the land gate went red on 14 of its tests).
  * the BINARY'S RUNNERS (GOH.AFFECTEDBINARY): a build-input change chooses every test running it.
  * the READERS of a changed path (lib/declared_reads.py, GOH.SELECTAUDIT, SELECTLOCKORDER): what
    a test, its tests/ helpers, or a scanner it points at the root globs, copies or declares.
  * EVERY test for a change to data pytest's load opens (lib/test_closure.py, GOH.BASEDATAREADS);
    for one to its CODE, its readers, the whole suite collected first (lib/fast_tests.py).
  * the RELEASE CANARY, after the tests, when a consumer NAMES a changed path from its own files
    (lib/consumer_reads.py, GOH.LANDMISS1119): no goh test runs a consumer's script, the canary
    does (GOH.PYTESTPIN: sys_updater reads `required_tools.py --floors`; green here, red in the
    land gate's canary at 502 s).
A deleted file is a changed path too: a test still naming it is the one that will go red.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lib.affected_verdict as av
from lib import (
    bin_reach_audit,
    binary_runners,
    consumer_reads,
    declared_reads,
    fast_tests,
    land_rustcov,  # GOH.LANDRUSTCOV: a Rust change's run measures its lines
    main_red,
    pre_cpu,  # GOH.PREWIDTH: the pytest tree's CPU, for the land record's pre_run
    pre_reuse,
    remote_lane,  # GOH.REMOTEWIRE: the Linux-safe share on the remote worker, concurrently
    source_closure,
    test_closure,
    test_slice,
    tree_walk,
)
from lib.step_timings import timed
from tui.lib import counted, info, ok

# Tree readers that neither a test name nor a tree-walking call in their own source gives away.
ALWAYS = ("tests/test_self_walk.py", "tests/test_python_from_head.py")
# The tree readers that judge the TEXT of every tracked file, whatever its kind: a diff of any file
# is theirs to read, release text included (lib/release_gate.py `names`, GOH.REUSEOFFLANDED5: the
# release set left the self-walk out, and v0.140.3's changelog heading broke its home-path count).
TEXT_JUDGES = ("tests/test_self_walk.py",)
SKIPPED: list[str] = []  # what the last enqueue choice's binary reach left out (GOH.BINREACH)
TREE_NAMED = re.compile(r"^\s*def (test_every_|test_no_)", re.MULTILINE)
# ... or that RUNS a tool over the whole checkout, `cwd=REPO_ROOT` (tests/test_gate_temp_leaks.py
# runs `structural.sh --full` there; GOH.PREPARITY: red in two lands no pre-step chose it for).
TREE_READ = tree_walk.TREE_READ


def _imports(text: str) -> set[str]:
    """Every module name an import in `text` could bind: `a.b` gives {a.b, a, b}; `from a import b`
    gives {a, a.b, b}. A file that does not parse imports nothing (its own test run says why)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
                names.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(node.module.split("."))
            for alias in node.names:
                names.add(f"{node.module}.{alias.name}")
                names.add(alias.name)
    return names


def _module_names(path: str) -> set[str]:
    """The names a test imports `path` by: `lib/admission.py` -> {lib.admission, admission}."""
    p = Path(path)
    if p.suffix != ".py" or p.name == "__init__.py":
        return set()
    return {".".join(p.with_suffix("").parts), p.stem}


standing = declared_reads.standing  # why a tree reader judges every diff at the enqueue
# The one naming rule (lib/source_closure.py): the `named` edge of a proven step's closure too.
_names_path = source_closure.names_path


# The graph (lib/test_closure.py); tests/_land_verdicts.py reads Closure here.
Closure = test_closure.Closure
_transitive = test_closure.transitive
_merge, _ran_nothing = av.merge_records, av.ran_nothing  # the pre-step's record (moved for the cap)


def _tests(root: Path) -> dict[str, str]:
    return {
        f"tests/{p.name}": p.read_text(encoding="utf-8", errors="replace")
        for p in sorted((root / "tests").glob("test_*.py"))
    }


def _helpers(root: Path) -> dict[str, str]:
    """tests/ modules that are not test files, by import name (`_self_walk`)."""
    return {
        p.stem: p.read_text(encoding="utf-8", errors="replace")
        for p in sorted((root / "tests").glob("*.py"))
        if not p.name.startswith("test_") and p.name != "conftest.py"
    }


# One reading of a module's units (lib/test_slice.py): what a statement binds, mentions, spans.
_segment, _refs, _bound = test_slice.segment, test_slice.refs, test_slice.bound


def reader_nodes(text: str, walking: tree_walk.Walking) -> list[str] | None:
    """The tests of a tree-reading file that read the tree, as pytest node ids relative to the
    file (`test_x`, `TestY::test_z`); None when the file must run whole (GOH.LANDPREDUP).

    A file is a reader when one of its tests is: the rest of it reads no tree, so a land whose
    diff neither names nor imports it cannot turn those red, and the full gate runs them anyway.
    Running whole files cost the pre-step 879 s of summed test time on goh's tree (2026-11-16);
    the reading tests alone are a fraction of it.

    A test reads the tree when its name is test_every_/test_no_, or its source (decorators and
    signature included) lists or walks the tree or mentions -- through any chain of the module's
    own functions, fixtures, classes and module-level names -- something that does, or a name
    from a tests/ helper in `walking`. A module-level statement that walks and binds nothing, a
    file that does not parse, or a reader file in which no test is found, runs whole."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    lines = text.splitlines()
    # names bound to a helper's walk of the root (`from _self_walk import walk`), and to a helper
    # def that walks only what it is handed (lib/tree_walk.py, GOH.CLONENOTJUDGE)
    walkers, given = tree_walk.imported(tree, walking)
    roots = tree_walk.aliases(tree.body)
    # (name, node) for every top-level and class-level definition or statement
    units: list[tuple[set[str], ast.AST]] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        units.append((_bound(node), node))
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                units.append((_bound(sub), sub))
    for bound, node in units:
        if TREE_READ.search(_segment(lines, node)) or tree_walk.hands_root(node, given, roots):
            if not bound:
                return None  # a module-level walk with no name to follow: the whole file
            walkers |= bound
    changed = True
    while changed:
        changed = False
        for bound, node in units:
            if bound - walkers and _refs(node) & walkers:
                walkers |= bound
                changed = True
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            if TREE_NAMED.search(f"def {node.name}") or node.name in walkers:
                found.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            whole = node.name in walkers and not any(
                isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name in walkers
                for s in node.body
            )
            for sub in node.body:
                test = isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name
                if not (test and test.startswith("test")):
                    continue
                if whole or TREE_NAMED.search(f"def {test}") or test in walkers:
                    found.append(f"{node.name}::{test}")
    return found or None


def _walking(root: Path) -> tree_walk.Walking:
    """{tests/ helper import name: what its defs walk, the root or what they are handed}
    (lib/tree_walk.py) for the helpers that list or walk a tree."""
    found = {name: tree_walk.walks(text) for name, text in _helpers(root).items()}
    return {name: w for name, w in found.items() if w}


def tree_readers(root: Path, tests: dict[str, str] | None = None) -> dict[str, str]:
    """{test file: why it is a tree reader}."""
    tests = _tests(root) if tests is None else tests
    walking = _walking(root)
    found: dict[str, str] = {}
    for rel, text in tests.items():
        if rel in ALWAYS:
            found[rel] = "a tree reader no pattern sees (ALWAYS)"
        elif TREE_NAMED.search(text):
            found[rel] = "defines a test_every_/test_no_ test"
        elif TREE_READ.search(text):
            found[rel] = "lists or walks the tree"
        elif via := tree_walk.reaches(text, walking):
            found[rel] = f"imports {via}, which lists or walks the tree"
    return found


def readers(root: Path, changed: list[str], tests: dict[str, str] | None = None, graph=None,
            reach: bool = False) -> dict[str, str]:  # fmt: skip
    """{test file: why} for the tests that READ a path in `changed` -- every rule of THE SET but
    the tree readers' standing run: the one notion of "a test reads this path", which the land's
    verdict reuse asks too (lib/pre_reuse.py, GOH.PRELANDTWICE). REACH (the enqueue's,
    GOH.BINREACH): a Rust change's binary tests by their stored profiles' reach; what that left
    out is SKIPPED's, for the audit."""
    tests = _tests(root) if tests is None else tests
    chosen: dict[str, str] = {}
    SKIPPED[:] = []
    for path in changed:
        if path.startswith("tests/test_") and path.endswith(".py") and path in tests:
            chosen.setdefault(path, "changed")
    for rel, text in tests.items():
        if rel in chosen:
            continue
        imported = _imports(text)
        for path in changed:
            if path == rel:
                continue
            if mods := sorted(_module_names(path) & imported):
                chosen[rel] = f"imports {mods[0]} ({path})"
                break
            if _names_path(text, path):
                chosen[rel] = f"names {path}"
                break
    if changed:
        graph = Closure(root) if graph is None else graph
        for rel in tests:
            if rel not in chosen and (why := _transitive(graph, rel, changed)):
                chosen[rel] = why
        SKIPPED[:] = binary_runners.choose(graph, tests, changed, chosen, reach=reach)
        declared_reads.choose(root, tests, changed, chosen, graph)
    return chosen


def affected(
    root: Path, changed: list[str], enqueue: bool | None = None, graph=None
) -> dict[str, str]:
    """{test file or node id: why it runs before the land} for a change to `changed` (paths from
    `root`). A file the diff chooses runs whole; a tree reader the diff does not choose runs only
    its reading tests (`reader_nodes`), whole when they cannot be told apart. At the ENQUEUE
    (GOH_LAND_PRE_ONLY; GOH.JUDGESBYREADS) a reader whose reads are known (lib/declared_reads.py
    `standing`) runs only when chosen by the diff; the land gate runs every tree reader. GRAPH is
    root's Closure when the caller built one (tests/_land_verdicts.py, GOH.CARRYSHADOW): the
    answer is the same, the graph's reach is read once."""
    enqueue = bool(os.environ.get("GOH_LAND_PRE_ONLY")) if enqueue is None else enqueue
    tests = _tests(root)
    if graph is None and enqueue:
        graph = Closure(root)
    chosen = readers(root, changed, tests, graph, reach=enqueue)
    walking = _walking(root)
    for rel, why in tree_readers(root, tests).items():
        if rel in chosen:
            continue  # chosen whole by the diff
        if enqueue and rel not in ALWAYS and not standing(graph, rel, tests[rel], root, walking):
            continue  # its reads are known, and the diff touches none of them
        nodes = None if rel in ALWAYS else reader_nodes(tests[rel], walking)
        if nodes is None:
            chosen[rel] = why
        else:
            chosen.update((f"{rel}::{n}", why) for n in nodes)
    chosen, SKIPPED[:] = binary_runners.once(chosen, SKIPPED)
    return dict(sorted({**chosen, **main_red.also(root)}.items()))  # GOH.MAINREDNOTICE


def changed_paths(root: Path, base: str) -> list[str]:
    """What HEAD changes against its merge base with `base` (renames as add + delete)."""
    out = subprocess.run(  # GIT_OPTIONAL_LOCKS: a reader never takes index.lock
        ["git", "-C", str(root), "diff", "--name-only", "--no-renames", f"{base}...HEAD"],
        capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    ).stdout  # fmt: skip
    return [line for line in out.splitlines() if line]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=".", help="the repository (default: the cwd)")
    ap.add_argument("--base", default="main", help="the revision the change is measured from")
    ap.add_argument("--changed", nargs="*", help="the changed paths, instead of the diff")
    ap.add_argument("--why", action="store_true", help="say why each file is chosen")
    ap.add_argument(
        "--readers-only", action="store_true", help="only the tests reading a changed path"
    )
    ap.add_argument(
        "--run", action="store_true", help="run the set with the root's tools/pytest.sh"
    )
    ap.add_argument("--structural", action="store_true", help="--run: structural.sh --full first")
    ap.add_argument("--rust-cov", action="store_true", help="--run: measure its changed Rust lines")
    ap.add_argument("--only", choices=("judges", "diff-cov"), help="the judges; the diff cov judge")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if args.run and (rc := fast_tests.run(root, affected(root, [], enqueue=False))):
        return rc  # every cheap judge, before the diff is read (GOH.PRESTEPFAILFAST): seconds
    changed = args.changed if args.changed is not None else changed_paths(root, args.base)
    chosen = (
        dict(sorted(readers(root, changed).items()))
        if args.readers_only
        else affected(root, changed)
    )
    if args.only == "diff-cov":  # GOH.LANDCHECK2 (land --check): the diff coverage judge alone
        return land_rustcov.judge(root, args.base, land_rustcov.selected(root, list(chosen)))
    if args.only == "judges":  # GOH.LANDCHECK2: the pre-step's first run, its whole-tree judges
        chosen = {n: chosen[n] for n in phases(root, chosen)[0]}
    if args.run:
        if args.structural and (rc := fast_tests.structural(root)):
            return rc
        whole = args.changed is None and not args.readers_only
        base = pre_reuse.base_sha(root, args.base) if whole else None  # ONE run per change: av
        rust = land_rustcov.plan(root, args.base) if args.rust_cov and whole else None
        rc = av.whole(
            root, list(chosen), base, whole, lambda: run(root, changed, chosen, base, rust)
        )
        return rc or canary(root, changed)
    for rel, why in chosen.items():
        print(f"{rel}  # {why}" if args.why else rel)
    return 0


def canary(root: Path, changed: list[str], repos: list[Path] | None = None) -> int:
    """The release canary when a consumer NAMES a changed path (lib/consumer_reads.py): no goh
    test runs a consumer's own script, the canary does (GOH.PYTESTPIN: green here, red in the land
    gate's canary at 502 s). Never inside a running goh suite (run's rule) or outside a goh tree."""
    script = root / "scripts" / "release_canary.py"
    if not script.is_file() or os.environ.get("GATES_OF_HECK_SUITE_RUNNER"):
        return 0
    found = consumer_reads.readers(root, changed, repos)
    if not found:
        return 0
    paths = sorted({p for v in found.values() for p in v})
    info(f"pre-land: {counted(len(found), 'consumer')} name {', '.join(paths)} -- the release "
         "canary, as the land gate runs it")  # fmt: skip
    return timed("pre-land: release canary", [sys.executable, str(script)])


def phases(root: Path, chosen: dict[str, str]) -> tuple[list[str], list[str]]:
    """(the JUDGES, the rest): the chosen nodes of the tree readers, which judge every file or key
    of the tree whatever the diff names, and then the tests the diff chose."""
    readers = tree_readers(root)
    judges = [n for n in chosen if n.split("::")[0] in readers]
    return judges, [n for n in chosen if n not in judges]


def run(root: Path, changed: list[str], chosen: dict, base: str | None = None, rust=None) -> int:
    """tools/pytest.sh over the set: the JUDGES first, in a run of their own, then the rest
    (GOH.PREPARITY: one run of both hit the 600 s deadline at load 175, and two judges it held went
    red in the land 444 s later; ADAPTLANES); a red judge is red before the slow tests start, and
    the runs' records are one (`_merge`). An EMPTY set, or one inside a running goh suite, runs
    nothing and says so (pytest.sh with no file is the full gate's job; a nested suite is refused).
    RUST (lib/land_rustcov.py): the binary's tests second, measured. GOH.REMOTEWIRE: the worker."""
    if rc := fast_tests.collects(root, changed):
        return rc
    if not chosen:
        why = f"no test is chosen by {counted(len(changed), 'changed path')}"
        ok(f"pre-land: {why}")
        _ran_nothing(why)
        return 0
    if os.environ.get("GATES_OF_HECK_SUITE_RUNNER") and not os.environ.get(
        "GATES_OF_HECK_SUITE_NESTED_OK"
    ):
        why = (
            f"{counted(len(chosen), 'test file')} not run: inside a running goh suite "
            f"(runner pid {os.environ['GATES_OF_HECK_SUITE_RUNNER']}), which runs them"
        )
        info(f"pre-land: {why}")
        _ran_nothing(why)
        return 0
    judges, rest = phases(root, chosen)
    judges, rest = land_rustcov.split(root, judges, rest) if rust else (judges, rest)
    info(
        f"pre-land: {counted(len(chosen), 'test')} -- {len(judges)} judging the whole tree "
        f"first, then {len(rest)} chosen by {counted(len(changed), 'changed path')}"
    )
    record = os.environ.get("GOH_LAND_VERDICTS_RECORD", "")
    first = Path(f"{record}.judges") if record and record != os.devnull else None
    rest, share = (rest, None) if rust else remote_lane.begin(root, rest, bool(judges), judges)
    for n, nodes in enumerate((judges, rest)):
        if not nodes and not (rust and n == 1):  # a Rust change measures its workspace's tests
            continue
        what = "judging the whole tree" if n == 0 else "the diff chose"
        plain = [*av.carrying(record), "bash", str(root / "tools" / "pytest.sh"), "-q", *nodes]
        argv = land_rustcov.argv(root, rust, nodes) if rust and n == 1 else plain
        with pre_cpu.counted():  # the run's tree, for the land record's `pre_run` (GOH.PREWIDTH)
            rc = timed(  # its own row (GOH.RELEASELANDGATE); no cwd: pytest.sh runs in its checkout
                f"pre-land: {len(nodes)} tests {what}", argv
            )
        if rc:
            return remote_lane.cancel(share) or rc
        if first and n == 0 and (rest or share) and Path(record).is_file():
            Path(record).replace(first)  # the rest's run writes its own; _merge joins them
    if first and first.is_file():
        _merge(Path(record), first)
    rc = remote_lane.end(root, share, record, _merge) or pre_reuse.stamp(record, base, list(chosen))
    return rc or bin_reach_audit.after(root, changed, list(SKIPPED))  # GOH.BINREACH, 1 in 10


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
