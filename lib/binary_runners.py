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
    from lib import gate_identity
except ImportError:  # run by path, not as a package
    import gate_identity  # type: ignore[no-redef]

CODE = re.compile(
    r"\b(?:run_goh|native_goh_path|native_goh|GOH_TEST_BIN|GOH_BIN|GOH_RESOLVED_BIN)\b"
    r"|\bbin/goh\b|[\"']bin[\"']\s*/\s*[\"']goh[\"']"
)
FIXTURES = {"goh", "native_goh"}  # the fixtures a test asks for by argument name


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
    """Add to `chosen` every test that runs the binary when `changed` holds a build input."""
    if build := next((p for p in changed if builds(p)), None):
        for rel, why in runners(graph, tests, build).items():
            chosen.setdefault(rel, why)
