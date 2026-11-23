"""No test dumps a whole environment to a file or into an assertion message (GOH.ENVDUMP).

Two ways the suite put a whole process environment, session tokens included, where it should not
be: tests/test_coverage_external.py wrote `env > seen.env` into its temp dir, and a killed run left
that dir behind (2026-11-12 17:10, 160 lines); GOH.CLEANOPEN's launch test asserted on a dict read
back from an `env` dump, and its red run printed the dict whole into the tool output. The one way
to capture an environment is tests/env_probe.py (names, plus the values of the names asserted on);
this scanner refuses the others. What it sees, in every tests/**/*.py:

  * shell in a string: `env` or `printenv` with no names, redirected (`env > f`, `printenv >> f`)
    or piped (`env | sort`), and `export -p` / `declare -px` redirected;
  * a bare run: an argv of exactly `["env"]`, `["/usr/bin/env"]`, `["printenv"]` ...;
  * Python: `os.environ` (or a name bound in the same function to a copy of it or to
    `hermetic_env(...)`) used WHOLE -- not subscripted, not `.get`/`.pop`, not only its names --
    inside an `assert` (its test, which pytest prints on failure, or its message) or in the
    arguments of a write (`write_text`, `write`, `writelines`, `dump`, `dumps`, `print`).

It cannot see a dict a helper returns whole (a read-back of a dump file); that is why the dump
itself is the thing refused. It landed as a ratchet seeded with every file's dumps; the seed is
empty now, so any dump is red (close-the-class).
"""
# goh-land-guard: test_no_test_dumps_an_environment

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SELF = Path(__file__).name  # its calibration cases are dumps, by design
# Its whole read of the tree (lib/declared_reads.py JUDGE_READS): the suite's own Python files.
JUDGE_READS = ("tests/*.py",)

_SHELL = re.compile(
    r"(?<![\w.!/-])(?:/usr/bin/)?(?:print)?env\s*(?:>>?|\|(?!\|))"
    r"|(?<![\w.-])export\s+-p\s*(?:>>?|\|)"
    r"|(?<![\w.-])declare\s+-p?x\s*(?:>>?|\|)"
)
_BARE = {"env", "/usr/bin/env", "printenv", "/usr/bin/printenv"}
_WRITES = {"write_text", "write", "writelines", "dump", "dumps", "print", "pformat", "pprint"}
# Uses that read one value or only the names: not a dump.
_PARTIAL_ATTRS = {"get", "pop", "setdefault", "keys", "__contains__"}
_NAMES_ONLY = {"sorted", "list", "set", "len", "tuple", "frozenset"}


def _is_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _is_env_source(node: ast.AST, names: frozenset[str] = frozenset()) -> bool:
    """An expression whose value is a whole environment: os.environ, a copy, hermetic_env(), a
    comprehension over its items -- or over, or a copy of, an environment already named (`names`):
    `dict(env, X="1")` of a fixture's environment is one, and a plain dict that prints whole."""

    def whole(n: ast.AST) -> bool:
        return _is_environ(n) or (isinstance(n, ast.Name) and n.id in names)

    if _is_environ(node):
        return True
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name) and f.id == "hermetic_env":
            return True
        if isinstance(f, ast.Attribute) and f.attr == "copy" and whole(f.value):
            return True
        if isinstance(f, ast.Name) and f.id == "dict" and node.args and whole(node.args[0]):
            return True
    if isinstance(node, ast.DictComp):
        return any(
            isinstance(g.iter, ast.Call)
            and isinstance(g.iter.func, ast.Attribute)
            and g.iter.func.attr == "items"
            and whole(g.iter.func.value)
            and not (g.ifs and all(_selects(c) for c in g.ifs))
            for g in node.generators
        )
    if isinstance(node, ast.Dict):
        return any(
            k is None and (whole(v) or _is_env_source(v, names))
            for k, v in zip(node.keys, node.values, strict=True)
        )
    return False


def _selects(test: ast.AST) -> bool:
    """A comprehension filter that KEEPS only the names it matches (`k.startswith("GOH_SHARE_")`,
    `k in KEEP`): a chosen few, not the environment. A filter that drops some keeps the rest."""
    if isinstance(test, ast.BoolOp):
        return (
            any(_selects(v) for v in test.values)
            if isinstance(test.op, ast.And)
            else all(_selects(v) for v in test.values)
        )
    if isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute):
        return test.func.attr == "startswith"
    return isinstance(test, ast.Compare) and all(isinstance(o, ast.In) for o in test.ops)


# What returns an environment that PRINTS redacted (tests/env_probe.py PrintSafeEnv): a fixture may
# hand one out, and pytest printing it in a failure's header or an assertion carries no value.
_SAFE = {"print_safe", "hermetic_env"}


def _is_fixture(func: ast.AST) -> bool:
    for dec in getattr(func, "decorator_list", ()):
        d = dec.func if isinstance(dec, ast.Call) else dec
        if (isinstance(d, ast.Attribute) and d.attr == "fixture") or (
            isinstance(d, ast.Name) and d.id == "fixture"
        ):
            return True
    return False


def _returns(func: ast.AST) -> list[ast.Return]:
    return [n for n in ast.walk(func) if isinstance(n, ast.Return) and n.value is not None]


def _is_safe(node: ast.AST) -> bool:
    f = node.func if isinstance(node, ast.Call) else None
    return getattr(f, "id", getattr(f, "attr", None)) in _SAFE


def fixtures(source: str) -> tuple[set[str], set[str]]:
    """The module's fixtures that hand out a whole environment: (print-safe ones, plain ones)."""
    safe, plain = set(), set()
    for func in ast.walk(ast.parse(source)):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_fixture(func):
            names = frozenset(_env_names(func))
            for r in _returns(func):
                if _is_safe(r.value):
                    safe.add(func.name)
                elif _is_env_source(r.value, names) or (
                    isinstance(r.value, ast.Name) and r.value.id in names
                ):
                    plain.add(func.name)
    return safe, plain


_ONE_VALUE = {"getenv", "putenv", "unsetenv"}


def _is_env_call(node: ast.AST) -> bool:
    """A call that BUILDS an environment, by the house naming (`hermetic_env()`, a test's own
    `_env(...)`, `_caller_env()`): its value is a whole environment wherever it is printed."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
    return name.lower().endswith("env") and name not in _ONE_VALUE


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _env_names(func: ast.AST, seeds: frozenset[str] = frozenset()) -> set[str]:
    """Names bound in `func` to a whole environment, from `seeds` (its environment parameters)."""
    names = set(seeds)
    while True:
        grown = set(names)
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and _is_env_source(node.value, frozenset(names)):
                grown |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        if grown == names:
            return names
        names = grown


def _whole(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """`node` (an environment) is used whole where it stands, not for one value or its names."""
    up = parents.get(node)
    if isinstance(up, ast.Subscript) and up.value is node:
        return False
    if isinstance(up, ast.Attribute) and up.attr in _PARTIAL_ATTRS:
        return False
    if isinstance(up, ast.Call) and isinstance(up.func, ast.Name) and up.func.id in _NAMES_ONLY:
        return False
    # `for k in os.environ`: the names. (`"X" in os.environ` IS whole in an assert's test: pytest
    # prints both sides of a failed membership test.)
    return not (isinstance(up, ast.comprehension) and up.iter is node)


_OPAQUE = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.Lambda)


def _walk_printed(node: ast.AST):
    """What pytest's assertion rewriting prints of a failed test: every sub-expression except the
    inside of a comprehension or lambda, which it reports only as the result."""
    yield node
    if not isinstance(node, _OPAQUE):
        for child in ast.iter_child_nodes(node):
            yield from _walk_printed(child)


def _sinks(tree: ast.AST) -> list[ast.AST]:
    """The expressions whose value can reach a file or an assertion's printed failure."""
    out: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            out.extend(_walk_printed(node.test))
            if node.msg is not None:
                out.extend(ast.walk(node.msg))
        elif isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in _WRITES:
                for arg in [*node.args, *(k.value for k in node.keywords if k.arg != "file")]:
                    out.extend(ast.walk(arg))
        elif isinstance(node, ast.FormattedValue):  # an f-string formats its value whole
            out.append(node.value)
    return out


def dumps(source: str, safe: Iterable[str] = (), plain: Iterable[str] = ()) -> list[int]:
    """The line of every whole-environment dump in `source`. `safe` and `plain` name the suite's
    fixtures that hand out an environment (`fixtures`): a test's parameter of a plain one prints
    whole, and a copy of either is a plain dict. A fixture returning a plain environment is a dump
    itself: pytest prints every argument of a failing test, values and all (GOH.PROVENENVDUMP: a
    land log carried the session's account id from test_proven's `env`)."""
    safe, plain = frozenset(safe), frozenset(plain)
    tree = ast.parse(source)
    found: set[int] = set()
    docstrings = {
        id(n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
    }  # prose about a dump runs nothing
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) in docstrings:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _SHELL.search(node.value):
                found.add(node.lineno)
        elif isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) == 1:
            only = node.elts[0]
            if isinstance(only, ast.Constant) and only.value in _BARE:
                found.add(node.lineno)
    parents = _parents(tree)
    scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for scope in [tree, *scopes]:
        params = {a.arg for a in getattr(scope, "args", ast.arguments()).args}
        seeds = frozenset(params & (safe | plain))
        printable = params & (safe - plain)  # a print-safe fixture's own parameter prints redacted
        env_names = _env_names(scope, seeds) - printable if scope is not tree else set()
        if scope is not tree and _is_fixture(scope):
            for r in _returns(scope):
                v = r.value
                if not _is_safe(v) and (
                    _is_env_source(v, frozenset(env_names) | seeds)
                    or (isinstance(v, ast.Name) and v.id in env_names)
                ):
                    found.add(r.lineno)
        for node in _sinks(scope):
            is_env = (
                _is_environ(node)
                or _is_env_call(node)
                or (
                    isinstance(node, ast.Name)
                    and node.id in env_names
                    and isinstance(node.ctx, ast.Load)
                )
            )
            if is_env and _whole(node, parents):
                found.add(node.lineno)
    return sorted(found)


def _resolved(sources: dict[str, str]) -> dict[str, tuple[set[str], set[str]]]:
    """Per module, the environment fixtures its parameters can name: its own, the ones it imports
    by name from another test module (`from test_proven import env`), and conftest's."""
    own = {stem: fixtures(src) for stem, src in sources.items()}
    out = {}
    for stem, src in sources.items():
        safe, plain = set(own[stem][0]), set(own[stem][1])
        for home in ("conftest", *_imported(src)):
            names = _imported(src).get(home) if home != "conftest" else None
            got = own.get(home, (set(), set()))
            safe |= {n for n in got[0] if names is None or n in names}
            plain |= {n for n in got[1] if names is None or n in names}
        out[stem] = (safe, plain)
    return out


def _imported(source: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.setdefault(node.module, set()).update(a.name for a in node.names)
    return out


def census() -> dict[str, int]:
    paths = {p.relative_to(TESTS).as_posix(): p for p in sorted(TESTS.rglob("*.py"))}
    paths = {rel: p for rel, p in paths.items() if p.name != SELF}
    sources = {rel: p.read_text(encoding="utf-8") for rel, p in paths.items()}
    by_stem = {Path(rel).stem: src for rel, src in sources.items() if "/" not in rel}
    resolved = _resolved(by_stem)
    out = {}
    for rel, src in sources.items():
        safe, plain = resolved.get(Path(rel).stem, (set(), set())) if "/" not in rel else ((), ())
        n = len(dumps(src, safe, plain))
        if n:
            out[rel] = n
    return out


CASES = """\
import os, json, subprocess
from pathlib import Path
a = 'env > "seen.env"; go'
b = f'printenv >> {p}'
c = "env | sort"
d = ["/usr/bin/env"]
Path("x").write_text(json.dumps(dict(os.environ)))
assert ok, os.environ
def t():
    env = hermetic_env(X="1")
    assert r.returncode == 0, env
    assert env == {}
e = "export -p > f"
ok1 = "#!/usr/bin/env bash\\nexec x"
ok2 = ["env", "-i", "PATH=/bin", "sh"]
ok3 = "printenv HOME > f"
ok4 = os.environ.get("HOME")
assert sorted(os.environ), sorted(os.environ)
def u():
    env = hermetic_env(X="1")
    assert env.get("X") == "1", env["X"]
    Path("n").write_text("\\n".join(k for k in os.environ))
    assert [n for n in ("A",) if n in os.environ] == []
    "a docstring that says env > f runs nothing"
ok5 = {"env": 1}["env"]
ok6 = "a || b"
def v():
    env = dict(os.environ, X="1")
    assert run(env=env).returncode == 0
assert "X" not in os.environ
assert run(_env(p)) == 0
ok7 = os.getenv("HOME")
assert os.getenv("HOME")
assert _env(p).get("X") == "1"
@pytest.fixture
def envf(tmp_path):
    e = {k: v for k, v in os.environ.items() if k != "X"}
    return e
@pytest.fixture
def okf():
    return print_safe({k: v for k, v in os.environ.items()})
ok8 = {k: v for k, v in os.environ.items() if k.startswith("GOH_SHARE_")}
def w(envf, okf):
    assert run(okf) == 0, okf
    assert run(envf) == 0
    lenv = dict(okf, X="1")
    assert run(lenv) == 0
    raise SystemExit(f"no X in {os.environ}")
"""


def test_the_detector_sees_every_spelling_and_nothing_else():
    """Calibration: each dump is one finding; every look-alike (lines 14-28) is none. Line 29: a
    call's arguments in an assert's test are printed whole; line 30: so is a membership test;
    line 31: so is an environment a helper builds (`_env(...)`), but not `os.getenv` (32-34).
    GOH.PROVENENVDUMP: a fixture returning a plain environment (38), which pytest prints with every
    failing test that takes it, and a test's use of it (45); a copy of a print-safe one (47); an
    f-string of one (48). A print-safe fixture (41) and a comprehension keeping chosen names (42)
    are none."""
    found = dumps(CASES, *fixtures(CASES))
    assert found == [3, 4, 5, 6, 7, 8, 11, 12, 13, 29, 30, 31, 38, 45, 47, 48]


def test_a_planted_dump_is_red(tmp_path):
    """The census bites on a real file: plant the coverage test's old probe, watch it counted."""
    planted = TESTS / "test_coverage_external.py"
    src = planted.read_text(encoding="utf-8")
    plant = src + '\n\nPROBE = f\'env > "{"seen.env"}"\'\n'
    assert len(dumps(plant)) == len(dumps(src)) + 1


def test_no_test_dumps_an_environment():
    """The class is closed. The seed it was calibrated against (2026-11-13, tests/env_dump_seed.json
    in history, widened by the env-building-call rule) named test_check_shell_lint.py 2,
    test_coverage_external.py 4, test_gate_tests_unbound.py 1, test_git_local_vars.py 2,
    test_goh_directory_spawns.py 1, test_gpu_lock.py 1, test_launch_app.py 1 and
    test_step_timings.py 6; each now captures names (tests/env_probe.py) or binds the
    env-carrying call before asserting on it."""
    now = census()
    assert now == {}, f"a whole environment dumped (use tests/env_probe.py): {now}"


def test_the_census_reads_the_suite():
    """A floor: a census that stopped parsing files reports clean. It reads every test module."""
    files = [p for p in TESTS.rglob("*.py") if p.name != SELF]
    assert len(files) > 300, len(files)
    for path in files:
        dumps(path.read_text(encoding="utf-8"))  # every module parses: none is silently skipped
