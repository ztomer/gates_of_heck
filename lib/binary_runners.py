"""Which code runs the goh binary, and which paths build it (GOH.AFFECTEDBINARY).

tools/affected_tests.py chooses, for a change to the binary's build inputs, every test that runs
the binary: such a test names no crates/ path and imports no changed module, so no other edge
reaches it (GRACESHAPES, 2026-11-17: the pre-step was green, the land gate red on
tests/test_check_moving_tags.py, which runs `goh moving-tags`). A test runs the binary when its
code asks for the `goh` or `native_goh` fixture, calls `run_goh` or `native_goh_path`, reads
GOH_TEST_BIN, GOH_BIN or GOH_RESOLVED_BIN, or names `bin/goh`; or when a helper or script it
reaches does (a gate script runs `goh step`). Prose is blanked before the read, so a docstring that
mentions one runs nothing.
"""

from __future__ import annotations

import ast
import re

try:
    from lib import crate_reach, gate_identity
except ImportError:  # run by path, not as a package
    import crate_reach  # type: ignore[no-redef]
    import gate_identity  # type: ignore[no-redef]

CODE = re.compile(
    r"\b(?:run_goh|native_goh_path|native_goh|GOH_TEST_BIN|GOH_BIN|GOH_RESOLVED_BIN)\b"
    r"|\bbin/goh\b|[\"']bin[\"']\s*/\s*[\"']goh[\"']"
)
FIXTURES = {"goh", "native_goh"}  # the fixtures a test asks for by argument name
WORD = re.compile(r"(?<![A-Za-z0-9_-])[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?![A-Za-z0-9_-])")


def builds(path: str) -> bool:
    """`path` is an input of the native binary's build (lib/gate_identity.py BUILD)."""
    return any(path == b or path.startswith(f"{b}/") for b in gate_identity.BUILD)


def names(code: str) -> bool:
    """`code` (prose blanked) names the binary or its harness."""
    return CODE.search(code) is not None


def asks(text: str) -> bool:
    """A function in `text` takes the `goh` or `native_goh` fixture."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    return any(
        a.arg in FIXTURES
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        for a in (*n.args.posonlyargs, *n.args.args, *n.args.kwonlyargs)
    )


def runs(text: str, code: str) -> bool:
    """A test file (`text`; `code`: the same with its prose blanked) runs the binary itself."""
    return asks(text) or names(code)


def words(rel: str, text: str, code: str) -> set[str]:
    """What a file could pass the binary as its subcommand: in Python each string literal that is
    one word (an argv element: `[goh, "layer-parity"]`, docstrings blanked in `code`), elsewhere
    every word of its code (a shell line runs `"$bin" structural`)."""
    if not rel.endswith(".py"):
        return set(WORD.findall(code))
    try:
        tree = ast.parse(code)
    except SyntaxError:
        tree = ast.parse("")
    return {
        n.value.strip()
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and WORD.fullmatch(n.value.strip())
    }


def invoked(graph, rel: str, text: str, subcommands: set[str]) -> set[str] | None:
    """The subcommands test `rel` can run: the words naming one in the test and in every file it
    reaches (a gate script runs the binary through a resolver the base sources, so it names none
    of the binary's keys: tests/test_gates_e2e.py runs gates/structural.sh). None (every
    subcommand) when a file that runs the binary itself names none: its argv comes from somewhere
    this cannot read."""
    found: set[str] = set()
    for dep in [rel, *graph.reach(rel)[0]]:
        dep_text = text if dep == rel else graph.texts.get(dep, "")
        own = words(dep, dep_text, graph.code(dep)) & subcommands
        if not own and runs(dep_text, graph.code(dep)) and (dep == rel or names(graph.code(dep))):
            return None
        found |= own
    return found or None


def runners(graph, tests: dict[str, str], build: str) -> dict[str, str]:
    """{test file: why a change to the build input `build` chooses it}: it runs the binary itself
    or through the nearest helper or script it reaches. `graph` is affected_tests' Closure."""
    out: dict[str, str] = {}
    for rel, text in tests.items():
        if runs(text, graph.code(rel)):
            via = ""
        elif dep := next((d for d in graph.reach(rel)[0] if names(graph.code(d))), None):
            via = f" through {dep}"
        else:
            continue
        out[rel] = f"runs the goh binary{via}, built from {build}"
    return out


def choose(graph, tests: dict[str, str], changed: list[str], chosen: dict[str, str]) -> None:
    """Add to `chosen` every test that runs the binary when `changed` holds a build input
    (GOH.AFFECTEDBINARY), narrowed to the tests whose subcommands the change can reach
    (lib/crate_reach.py, GOH.AFFECTEDBINARY2). A change whose reach cannot be told (main.rs,
    Cargo.toml, a module every run reaches) still chooses them all."""
    crate = crate_reach.Crate(graph.root)
    inputs = [p for p in changed if builds(p) or p in crate.includes]
    if not inputs:
        return
    reach: set[str] = set()
    whole: str | None = None
    for path in inputs:
        subs = crate.subcommands(path)
        if subs is None:
            whole = whole or path
        else:
            reach |= subs
    names = {crate_reach.kebab(v) for v in crate.variants}
    for rel, why in runners(graph, tests, whole or inputs[0]).items():
        if whole is None:
            subs = invoked(graph, rel, tests[rel], names)
            if not reach or (subs is not None and not subs & reach):
                continue  # a test-only source reaches no subcommand
            via = "every subcommand" if subs is None else f"goh {min(subs & reach)}"
            chosen.setdefault(rel, f"{why} ({via})")
        else:
            chosen.setdefault(rel, why)
