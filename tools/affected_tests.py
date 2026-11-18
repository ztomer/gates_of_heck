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
    function, or whose source (or a tests/ helper it imports) lists or walks the tracked tree
    (`git ls-files`, `rglob(`, `os.walk(`, a glob or
    iterdir of a REPO_ROOT directory), plus ALWAYS (readers no pattern sees). A changed path
    can break a tree reader whatever it names, so they run on every land.
  * the TRANSITIVE closure of each test (GOH.AFFECTEDTRANSITIVE): what a test imports, what that
    imports (tests/ helpers, checks/, lib/, tools/ modules: lib/source_closure.py reads the
    edges, the one dependency reader), and a script it names by path with what that script sources
    -- a test is chosen when a changed file is anywhere in that closure, or a file in it NAMES a
    changed path. tests/test_gate_slot_race.py imports checks/_gate_slot_probe.py, which copies
    and patches lib/gate_slot.py by name: a change to the wait text there is the race test's,
    though the test says neither (SLOTFAIR land 1, 706 s). A naming edge (a file naming a script
    it runs) is followed from a test and the tests/ and checks/ helpers it reaches, never from a
    lib/ or tools/ module's prose, and never twice in a row: a script's own mentions are not run.
    What a reached SHELL script runs by a variable-rooted path (`"$GOH_PY" "$ROOT/lib/x.py"`) is
    an edge, a file pytest loads before every test included (GOH.PREPARITY: gates/_rust_proven.sh
    runs lib/gate_identity.py, which tests/_land_verdicts.py imports; the pre-step missed
    tests/test_rust_gate.py and the land gate went red on 14 of its tests).
  * the BINARY'S RUNNERS (GOH.AFFECTEDBINARY): a build-input change chooses every test running it.
  * the READERS of a changed path (GOH.SELECTAUDIT, lib/declared_reads.py): a test globbing or
    copying a spelled directory, or declaring `TREE_READS = (...)`, for a path under what it reads.
A deleted file is a changed path too: a test still naming it is the one that will go red.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import binary_runners, declared_reads, fast_tests, source_closure, test_closure
from tui.lib import counted, info, ok

# Tree readers that neither a test name nor a tree-walking call in their own source gives away.
ALWAYS = ("tests/test_self_walk.py", "tests/test_python_from_head.py")
TREE_NAMED = re.compile(r"^\s*def (test_every_|test_no_)", re.MULTILINE)
# ... or that RUNS a tool over the whole checkout, `cwd=REPO_ROOT` (tests/test_gate_temp_leaks.py
# runs `structural.sh --full` there; GOH.PREPARITY: red in two lands no pre-step chose it for).
TREE_READ = re.compile(
    r"ls-files|ls_files|\.rglob\(|os\.walk\(|REPO_ROOT\b[^\n]*\.(?:glob|iterdir)\("
    r"|cwd=(?:str\()?REPO_ROOT\)?\s*[,)]"
)


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


# The one naming rule (lib/source_closure.py): the `named` edge of a proven step's closure too.
_names_path = source_closure.names_path


# The graph (lib/test_closure.py); tests/_land_verdicts.py reads Closure here.
Closure = test_closure.Closure
PY_DIRS = test_closure.PY_DIRS
_transitive = test_closure.transitive


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


def _segment(lines: list[str], node: ast.AST) -> str:
    """`node`'s source, its decorators included (a parametrize over `REPO_ROOT.rglob(...)`)."""
    first = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
    return "\n".join(lines[first - 1 : node.end_lineno])


def _refs(node: ast.AST) -> set[str]:
    """Every name `node` mentions: bare names, attributes (`self.walk`) and argument names (the
    fixtures a test asks for)."""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
    return out


def _bound(node: ast.stmt) -> set[str]:
    """The names a module- or class-level statement binds."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, ast.alias):
            out.add((n.asname or n.name).split(".")[0])
    return out


def reader_nodes(text: str, walking: set[str]) -> list[str] | None:
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
    walkers: set[str] = set(walking)
    for node in tree.body:  # names a walking helper binds here (`from _self_walk import walk`)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = {a.name for a in node.names} | (
                {node.module} if isinstance(node, ast.ImportFrom) and node.module else set()
            )
            if any(m.split(".")[0] in walking for m in mods):
                walkers |= _bound(node)
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
        if TREE_READ.search(_segment(lines, node)):
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


def _walking(root: Path) -> set[str]:
    """The tests/ helpers that list or walk the tree, by import name."""
    return {name for name, text in _helpers(root).items() if TREE_READ.search(text)}


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
        elif via := sorted(walking & _imports(text)):
            found[rel] = f"imports {via[0]}, which lists or walks the tree"
    return found


def readers(root: Path, changed: list[str], tests: dict[str, str] | None = None) -> dict[str, str]:
    """{test file: why} for the tests that READ a path in `changed` -- every rule of THE SET but
    the tree readers' standing run: the one notion of "a test reads this path", which the land's
    verdict reuse asks too (lib/pre_reuse.py, GOH.PRELANDTWICE)."""
    tests = _tests(root) if tests is None else tests
    chosen: dict[str, str] = {}
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
        graph = Closure(root)
        for rel in tests:
            if rel not in chosen and (why := _transitive(graph, rel, changed)):
                chosen[rel] = why
        binary_runners.choose(graph, tests, changed, chosen)
        declared_reads.choose(root, tests, changed, chosen)
    return chosen


def affected(root: Path, changed: list[str]) -> dict[str, str]:
    """{test file or node id: why it runs before the land} for a change to `changed` (paths from
    `root`). A file the diff chooses runs whole; a tree reader the diff does not choose runs only
    its reading tests (`reader_nodes`), whole when they cannot be told apart."""
    tests = _tests(root)
    chosen = readers(root, changed, tests)
    walking = _walking(root)
    for rel, why in tree_readers(root, tests).items():
        if rel in chosen:
            continue  # chosen whole by the diff
        nodes = None if rel in ALWAYS else reader_nodes(tests[rel], walking)
        if nodes is None:
            chosen[rel] = why
        else:
            chosen.update((f"{rel}::{n}", why) for n in nodes)
    return dict(sorted(chosen.items()))


def changed_paths(root: Path, base: str) -> list[str]:
    """What HEAD changes against its merge base with `base` (renames as add + delete)."""
    out = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", "--no-renames", f"{base}...HEAD"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},  # a reader never takes index.lock
    ).stdout
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
    ap.add_argument(
        "--structural", action="store_true", help="--run: gates/structural.sh --full first"
    )
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if args.run and (rc := fast_tests.run(root, affected(root, []))):
        return rc  # the cheap judges, before the diff is even read (GOH.PRESTEPFAILFAST)
    changed = args.changed if args.changed is not None else changed_paths(root, args.base)
    chosen = (
        dict(sorted(readers(root, changed).items()))
        if args.readers_only
        else affected(root, changed)
    )
    if args.run:
        if args.structural and (rc := structural(root)):
            return rc
        whole = args.changed is None and not args.readers_only
        return run(root, changed, chosen, _base_sha(root, args.base) if whole else None)
    for rel, why in chosen.items():
        print(f"{rel}  # {why}" if args.why else rel)
    return 0


def structural(root: Path) -> int:
    """The structural gates over the whole branch, before any test (GOH.PREPARITY): the 500-line
    cap, the roadmap, the seeds -- seconds, where the land gate met them after ~10 min of suite
    (GOH.AFFECTEDBINARY's land). This checkout's gates/structural.sh, as the land gate runs it."""
    info("pre-land: the structural gates over the branch first (gates/structural.sh --full)")
    script = Path(__file__).resolve().parent.parent / "gates" / "structural.sh"
    from checks._gitutil import local_env_vars  # git's own list of what binds it to one repo

    env = {k: v for k, v in os.environ.items() if k not in local_env_vars()}  # the ROOT's git
    return subprocess.run(
        ["bash", str(script), "--full"], cwd=root, env=env, check=False, stdin=subprocess.DEVNULL
    ).returncode


def phases(root: Path, chosen: dict[str, str]) -> tuple[list[str], list[str]]:
    """(the JUDGES, the rest): the chosen nodes of the tree readers, which judge every file or key
    of the tree whatever the diff names, and then the tests the diff chose."""
    readers = tree_readers(root)
    judges = [n for n in chosen if n.split("::")[0] in readers]
    return judges, [n for n in chosen if n not in judges]


def _base_sha(root: Path, base: str) -> str | None:
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    return out.stdout.strip() or None


def _stamp(record: str, base: str | None) -> None:
    """The record says it is the WHOLE selection the diff from BASE chose (GOH.SUITEONCE): the gate
    of the tree it judged takes it for the suite only then (lib/pre_reuse.py `suite`)."""
    if not base or not record or record == os.devnull:
        return
    try:
        rec = json.loads(Path(record).read_text())
    except (OSError, ValueError):
        return
    if isinstance(rec, dict):
        Path(record).write_text(json.dumps({**rec, "base": base, "selection": "affected"}))


def run(root: Path, changed: list[str], chosen: dict[str, str], base: str | None = None) -> int:
    """tools/pytest.sh over the set: the JUDGES first, in a run of their own, then the rest
    (GOH.PREPARITY: one run of both reached the 600 s deadline at load 175 with no verdict, and
    two judges it held went red in the land 444 s later; ADAPTLANES). A red judge is a red
    verdict before the slow tests start; the two runs' records are one (`_merge`). An EMPTY set
    runs nothing: pytest.sh with no file runs the whole suite, which is the full gate's job, never
    the pre-step's. Inside a running goh suite (a fixture land of a goh copy) it runs nothing
    either and says so: pytest.sh refuses a nested suite, and the suite around it runs these."""
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
    info(
        f"pre-land: {counted(len(chosen), 'test')} -- {len(judges)} judging the whole tree "
        f"first, then {len(rest)} chosen by {counted(len(changed), 'changed path')}"
    )
    record = os.environ.get("GOH_LAND_VERDICTS_RECORD", "")
    first = Path(f"{record}.judges") if record and record != os.devnull else None
    for n, nodes in enumerate((judges, rest)):
        if not nodes:
            continue
        rc = subprocess.run(
            ["bash", str(root / "tools" / "pytest.sh"), "-q", *nodes],
            check=False,  # no cwd: pytest.sh runs in its own checkout, the root
            stdin=subprocess.DEVNULL,
        ).returncode
        if rc:
            return rc
        if first and n == 0 and rest and Path(record).is_file():
            Path(record).replace(first)  # the rest's run writes its own; _merge joins them
    if first and first.is_file():
        _merge(Path(record), first)
    _stamp(record, base)
    return 0


def _merge(record: Path, first: Path) -> None:
    """The judges' record joined into the rest's, when both judged the same tree and tip."""
    try:
        a, b = json.loads(first.read_text()), json.loads(record.read_text())
    except (OSError, ValueError):
        a, b = None, None
    if a and b and (a.get("identity"), a.get("sha")) == (b.get("identity"), b.get("sha")):
        b["passed"] = {**a.get("passed", {}), **b.get("passed", {})}
        record.write_text(json.dumps(b))
    elif a and not record.is_file():
        first.replace(record)
        return
    first.unlink(missing_ok=True)


def _ran_nothing(why: str) -> None:
    """The land's record when no suite ran to write it: no verdict, and why. tools/land.sh refuses a
    green pre-step with no record in a tree whose suite records one (GOH.VERDICTCARRY2)."""
    path = os.environ.get("GOH_LAND_VERDICTS_RECORD", "")
    if not path or path == os.devnull:
        return
    Path(path).write_text(json.dumps({"identity": None, "sha": None, "passed": {}, "ran": why}))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
