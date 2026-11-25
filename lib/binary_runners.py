"""Which code runs the goh binary, and which paths build it (GOH.AFFECTEDBINARY, GOH.BUILDINCLUDES).

tools/affected_tests.py chooses, for a change to the binary's build inputs, every test that runs
the binary: such a test names no crates/ path and imports no changed module, so no other edge
reaches it (GRACESHAPES, 2026-11-17: the pre-step was green, the land gate red on
tests/test_check_moving_tags.py, which runs `goh moving-tags`). A test runs the binary when its
code asks for the `goh` or `native_goh` fixture, calls `run_goh` or `native_goh_path`, reads
GOH_TEST_BIN, GOH_BIN or GOH_RESOLVED_BIN, or names `bin/goh`; or when a helper or script it
reaches does (a gate script runs `goh step`). Prose is blanked before the read, so a docstring that
mentions one runs nothing.

GOH.AFFECTEDBINARY: which code runs the goh binary (the `goh`/`native_goh` fixture, `run_goh`,
`native_goh_path`, `GOH_TEST_BIN`/`GOH_BIN`/`GOH_RESOLVED_BIN`, `bin/goh`; prose blanked) and which
paths build it (`build_input`: the four roots and each file a crate embeds, derived by
`gate_identity.build_inputs`, one reader for the affected edge and the verdict carry,
GOH.BUILDINCLUDES); `runners` is the set `tools/affected_tests.py` chooses for a build-input
change: a test runs the binary itself or through a helper or script it reaches. Pinned by
`test_affected_binary.py`, `test_build_includes.py`.
"""

from __future__ import annotations

import ast
import functools
import re

try:
    from lib import bin_reach, crate_reach, gate_identity
except ImportError:  # run by path, not as a package
    import bin_reach  # type: ignore[no-redef]
    import crate_reach  # type: ignore[no-redef]
    import gate_identity  # type: ignore[no-redef]

CODE = re.compile(
    r"\b(?:run_goh|native_goh_path|native_goh|GOH_TEST_BIN|GOH_BIN|GOH_RESOLVED_BIN)\b"
    r"|\bbin/goh\b|[\"']bin[\"']\s*/\s*[\"']goh[\"']"
)
FIXTURES = {"goh", "native_goh"}  # the fixtures a test asks for by argument name
WORD = re.compile(r"(?<![A-Za-z0-9_-])[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?![A-Za-z0-9_-])")


def build_inputs_in(root, changed: list[str]) -> list[str]:
    """Each of `changed` that is an input of the native binary's build at `root`: the four roots
    and each file a crate embeds (gate_identity.build_inputs, derived from the crates' include
    literals, GOH.BUILDINCLUDES). The one reader every consumer of "the build changed" asks: this
    edge, the verdict carry (tests/_land_verdicts.py)."""
    inputs = gate_identity.build_inputs(root)
    return [p for p in changed if any(p == b or p.startswith(f"{b}/") for b in inputs)]


def build_input(root, changed: list[str]) -> str | None:
    """The first of `changed` that is an input of the build at `root` (build_inputs_in), if any."""
    return next(iter(build_inputs_in(root, changed)), None)


@functools.cache  # pure in its code, asked of one file by every test that reaches it
def names(code: str) -> bool:
    """`code` (prose blanked) names the binary or its harness."""
    return CODE.search(code) is not None


@functools.cache  # pure in its text: one parse per file, not one per test that reaches it
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


def words(rel: str, code: str) -> frozenset[str]:
    """What a file could pass the binary as its subcommand (`_words`, read once per file: a
    helper every test reaches was parsed once per test, 4258 parses for one choice,
    GOH.CEILINGHEADROOM2)."""
    return _words(rel.endswith(".py"), code)


@functools.cache
def _words(python: bool, code: str) -> frozenset[str]:
    """What a file could pass the binary as its subcommand: in Python each string literal that is
    one word (an argv element: `[goh, "layer-parity"]`, docstrings blanked in `code`), elsewhere
    every word of its code (a shell line runs `"$bin" structural`)."""
    if not python:
        return frozenset(WORD.findall(code))
    try:
        tree = ast.parse(code)
    except SyntaxError:
        tree = ast.parse("")
    return frozenset(
        n.value.strip()
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and WORD.fullmatch(n.value.strip())
    )


def _reached(graph, rel: str) -> dict:
    """What `rel` reaches by imports, sources and the scripts it names -- not a shell script's
    variable-rooted runs (lib/test_closure.py `uses`), which the binary's subcommand reading does
    not narrow (GOH.PREPARITY: they took a scanner change's runners from 188 files to 243)."""
    return graph.reach(rel, runs=False)[0]


def invoked(graph, rel: str, text: str, subcommands: set[str]) -> set[str] | None:
    """The subcommands test `rel` can run: the words naming one in the test and in every file it
    reaches (a gate script runs the binary through a resolver the base sources, so it names none
    of the binary's keys: tests/test_gates_e2e.py runs gates/structural.sh). None (every
    subcommand) when a file that runs the binary itself names none: its argv comes from somewhere
    this cannot read."""
    found: set[str] = set()
    for dep in [rel, *_reached(graph, rel)]:
        dep_text = text if dep == rel else graph.texts.get(dep, "")
        own = words(dep, graph.code(dep)) & subcommands
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
        elif dep := next((d for d in _reached(graph, rel) if names(graph.code(d))), None):
            via = f" through {dep}"
        else:
            continue
        out[rel] = f"runs the goh binary{via}, built from {build}"
    return out


def _durations() -> dict[str, float]:
    """The suite's duration table (lib/fast_tests.py `load`): the node ids of each test file."""
    try:
        from lib import fast_tests
    except ImportError:  # run by path: tui is beside lib
        import fast_tests  # type: ignore[no-redef]
    return fast_tests.load()


def choose(
    graph, tests: dict[str, str], changed: list[str], chosen: dict[str, str], reach: bool = False
) -> list[str]:
    """Add to `chosen` every test that runs the binary when `changed` holds a build input
    (GOH.AFFECTEDBINARY), narrowed to the tests whose subcommands the change can reach
    (lib/crate_reach.py, GOH.AFFECTEDBINARY2). A change whose reach cannot be told (main.rs,
    Cargo.toml, a module every run reaches) still chooses them all. REACH (the enqueue's mode,
    GOH.BINREACH): only the runners' tests whose stored profile reaches the change
    (lib/bin_reach.py); the return is what that left out ([] otherwise), for the audit."""
    crate = crate_reach.Crate(graph.root)
    built = set(build_inputs_in(graph.root, changed))
    inputs = [p for p in changed if p in built or p in crate.includes]
    if not inputs:
        return []
    reach_set: set[str] = set()
    whole: str | None = None
    for path in inputs:
        subs = crate.subcommands(path)
        unplaced = path in built and path not in crate.includes and path not in crate.module
        if subs is None or (unplaced and not subs):  # an embed the reach cannot place: every one
            whole = whole or path
        else:
            reach_set |= subs
    names = {crate_reach.kebab(v) for v in crate.variants}
    picked: dict[str, str] = {}
    for rel, why in runners(graph, tests, whole or inputs[0]).items():
        if rel in chosen:
            continue
        if whole is None:
            subs = invoked(graph, rel, tests[rel], names)
            if not reach_set or (subs is not None and not subs & reach_set):
                continue  # a test-only source reaches no subcommand
            via = "every subcommand" if subs is None else f"goh {min(subs & reach_set)}"
            picked[rel] = f"{why} ({via})"
        else:
            picked[rel] = why
    skipped: list[str] = []
    hit = bin_reach.touched(crate, inputs) if reach and bin_reach.enabled() and picked else None
    if hit is not None and (stored := bin_reach.Reach.load(repo=graph.root)).by_hash:
        known: dict[str, list[str]] = {}
        for n in _durations():
            known.setdefault(n.split("::", 1)[0], []).append(n)
        picked, skipped = bin_reach.narrow(crate, stored, hit, picked, tests, known)
    for entry, why in picked.items():
        chosen.setdefault(entry, why)
    return skipped


def once(chosen: dict[str, str], skipped: list[str]) -> tuple[dict[str, str], list[str]]:
    """CHOSEN with a node dropped where its file runs whole (pytest would run it twice), and
    SKIPPED less what CHOSEN runs after all (another rule chose it)."""
    whole = {k for k in chosen if "::" not in k}
    kept = {k: v for k, v in chosen.items() if "::" not in k or k.split("::")[0] not in whole}
    return kept, [n for n in skipped if n not in kept and n.split("::")[0] not in whole]
