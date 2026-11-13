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
itself is the thing refused. The seed, tests/env_dump_seed.json, is exactly the dumps each file
held when this landed; a NEW dump is red, and so is an entry above its file's count, so the list
only shrinks (close-the-class).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SEED = TESTS / "env_dump_seed.json"
SELF = Path(__file__).name  # its calibration cases are dumps, by design

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


def _is_env_source(node: ast.AST) -> bool:
    """An expression whose value is a whole environment: os.environ, a copy, hermetic_env()."""
    if _is_environ(node):
        return True
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name) and f.id == "hermetic_env":
            return True
        if isinstance(f, ast.Attribute) and f.attr == "copy" and _is_environ(f.value):
            return True
        if isinstance(f, ast.Name) and f.id == "dict" and node.args and _is_environ(node.args[0]):
            return True
    if isinstance(node, ast.Dict):
        return any(
            k is None and _is_env_source(v) for k, v in zip(node.keys, node.values, strict=True)
        )
    return False


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _env_names(func: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and _is_env_source(node.value):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


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
    return out


def dumps(source: str) -> list[int]:
    """The line of every whole-environment dump in `source`."""
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
        env_names = _env_names(scope) if scope is not tree else set()
        for node in _sinks(scope):
            is_env = _is_environ(node) or (
                isinstance(node, ast.Name)
                and node.id in env_names
                and isinstance(node.ctx, ast.Load)
            )
            if is_env and _whole(node, parents):
                found.add(node.lineno)
    return sorted(found)


def census() -> dict[str, int]:
    out = {}
    for path in sorted(TESTS.rglob("*.py")):
        if path.name == SELF:
            continue
        n = len(dumps(path.read_text(encoding="utf-8")))
        if n:
            out[path.relative_to(TESTS).as_posix()] = n
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
"""


def test_the_detector_sees_every_spelling_and_nothing_else():
    """Calibration: each dump is one finding; every look-alike (lines 14-28) is none. Line 29: a
    call's arguments in an assert's test are printed whole; line 30: so is a membership test."""
    assert dumps(CASES) == [3, 4, 5, 6, 7, 8, 11, 12, 13, 29, 30]


def test_a_planted_dump_is_red(tmp_path):
    """The census bites on a real file: plant the coverage test's old probe, watch it counted."""
    planted = TESTS / "test_coverage_external.py"
    src = planted.read_text(encoding="utf-8")
    plant = src + '\n\nPROBE = f\'env > "{"seen.env"}"\'\n'
    assert len(dumps(plant)) == len(dumps(src)) + 1


def test_no_test_dumps_an_environment_beyond_the_seed():
    seed = json.loads(SEED.read_text(encoding="utf-8"))["files"]
    now = census()
    grown = {f: n for f, n in now.items() if n > seed.get(f, 0)}
    assert not grown, f"a whole environment dumped (use tests/env_probe.py): {grown}"
    stale = {f: (n, now.get(f, 0)) for f, n in seed.items() if now.get(f, 0) < n}
    assert not stale, f"lower these seed entries to the file's count (seed, now): {stale}"


def test_the_census_reads_the_suite():
    """A floor: a census that stopped parsing files reports clean. It reads every test module."""
    files = [p for p in TESTS.rglob("*.py") if p.name != SELF]
    assert len(files) > 300, len(files)
    for path in files:
        dumps(path.read_text(encoding="utf-8"))  # every module parses: none is silently skipped
