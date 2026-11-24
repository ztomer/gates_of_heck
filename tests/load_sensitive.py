"""The tests that can fail under load and pass without it (GOH.LOADEDSUBSET).

The post-land loaded run once ran the whole suite under 16 spinners, 470-570 s, then this set
alone; it caught nothing in 13 runs and is gone (GOH.LOADEDDROP). This module DERIVES the tests
whose verdict can move with the box's load, and tests/test_load_sensitive.py refuses -- at commit,
through gates/staged_guards.sh -- a test the derivation names that does not carry the marker, so a
new waiting test is declared and the set (`pytest -m load_sensitive`) cannot rot as tests are added.

A test is load-sensitive when it, or a helper or fixture it reaches, does one of these (SIGNALS):

  * sleep     -- sleeps (time.sleep, asyncio.sleep): a state stood for by an interval;
  * clock     -- reads a clock (time.monotonic, perf_counter, time.time) or asserts an elapsed time
                 (timing_bounds.assert_sooner);
  * poll      -- polls until a state with timing_bounds.GiveUp;
  * spawn     -- starts a process that runs BESIDE the test (subprocess.Popen, os.fork): a race with
                 it is a race the scheduler decides;
  * thread    -- a Thread, Process, Event or Timer: the same race inside the interpreter (an
                 executor's map, joined whole with no bound, orders nothing and is not one);
  * signal    -- signals a process (os.kill, killpg, send_signal, terminate, signal.alarm);
  * wait      -- waits on another actor with a bound (`.wait()`, `.join(n)`, `.get(timeout=)`,
                 `.communicate(timeout=)`, `.acquire(timeout=)`);
  * deadline  -- hands the code under test a time bound: a string naming one (`--timeout`,
                 `--stall`, `--grace`, `--wait-max`, `GOH_STEP_TIMEOUT`, any *_DEADLINE/_CEILING);
  * tight     -- `timeout=` a literal under TIGHT_S seconds (the bounds history saw expire under
                 load were 3, 10 and 15 s; tests/wall_clock_lint.py refuses NEW literals).

"Reaches" follows names: a function of the same module, a method through `self.`, a name imported
from another tests/ module (`from load_kit import alive`, `load_kit.alive`), and a fixture the test
takes as a parameter, from its module or tests/conftest.py.

What it does NOT see, so a reader knows the hole: a deadline inside the code under test that the
test does not name (a gate's own `ps` under a 30 s cap), a test that is slow enough to meet the
drift guard's stretched ceiling, and autouse fixtures (they would make every test sensitive). Such
a test carries the marker by hand WITH a reason, `@pytest.mark.load_sensitive(reason="...")`; a
mark the derivation does not back and that gives no reason is stale, and refused. A synchronous
`subprocess.run` with no deadline is not a signal: a child run to completion cannot be timed out
by load, only slowed.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

MARK = "load_sensitive"
TIGHT_S = 30.0
SIGNALS = ("sleep", "clock", "poll", "spawn", "thread", "signal", "wait", "deadline", "tight")

# A wait, sleep or spawn is a call to a KNOWN API (GOH.TIMINGDETECT), never a name alone: a module's
# function is known by the module it is called on or imported from (`time.sleep`, `from time import
# sleep`, `import subprocess as sp; sp.Popen`), so `psutil.Process(pid)`, a fake clock's
# `clock.sleep(5)` and a fixture's `fake.monotonic()` wait for nothing. A method on an object is
# known by its name and its arguments (below); the house's own timing APIs by their names.
_MODULE_API = {
    "time": {
        "sleep": "sleep",
        **dict.fromkeys(
            ("monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns", "time", "time_ns"),
            "clock",
        ),
    },
    "asyncio": {"sleep": "sleep", "Event": "thread"},
    "subprocess": {"Popen": "spawn"},
    "os": {"fork": "spawn", "kill": "signal", "killpg": "signal"},
    "signal": {"alarm": "signal"},
    "threading": {"Thread": "thread", "Event": "thread", "Timer": "thread"},
    "multiprocessing": {"Process": "thread", "Event": "thread"},
}
_HOUSE_API = {"assert_sooner": "clock", "GiveUp": "poll"}  # tests/timing_bounds.py
_METHODS = {"kill": "signal", "send_signal": "signal", "terminate": "signal"}  # Popen's, a handle's
_WAITS = {"wait", "join", "communicate", "get", "acquire"}
# `get` and `acquire` are also a dict's lookup with a default (`seed.get(name, 0)`,
# GOH.LOADSENSEGET): a number passed to them positionally is a timeout only after a literal block
# flag (`queue.get(True, 1)`, `lock.acquire(True, 1)`). Any other wait's literal is its bound.
_LOOKALIKES = {"get", "acquire"}


def _timed_positionally(n: ast.Call, name: str) -> bool:
    if name not in _LOOKALIKES:
        return any(_literal(a) is not None for a in n.args)
    flag = n.args[0] if n.args else None
    is_flag = isinstance(flag, ast.Constant) and isinstance(flag.value, bool)
    return is_flag and len(n.args) > 1 and _literal(n.args[1]) is not None


_DEADLINE = re.compile(
    r"^--(timeout|stall|grace|deadline|ceiling|wait-max|max-wait|budget)(=|$)"
    r"|^[A-Z0-9_]*(TIMEOUT|DEADLINE|STALL|CEILING|PATIENCE)[A-Z0-9_]*$"
)


def _literal(node: ast.AST) -> float | None:
    """The number a literal (or arithmetic over literals) spells, else None."""
    if isinstance(node, ast.Constant):
        ok = isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
        return float(node.value) if ok else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _literal(node.operand)
        return None if v is None else -v
    if isinstance(node, ast.BinOp):
        a, b = _literal(node.left), _literal(node.right)
        if a is None or b is None:
            return None
        ops = {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b}
        if isinstance(node.op, ast.Div):
            return a / b if b else None
        return ops.get(type(node.op))
    return None


def _call_name(call: ast.Call) -> tuple[str, str]:
    f = call.func
    if isinstance(f, ast.Attribute):
        base = f.value.id if isinstance(f.value, ast.Name) else ""
        return f.attr, base
    return (f.id, "") if isinstance(f, ast.Name) else ("", "")


def origins(tree: ast.AST) -> dict[str, tuple[str, str]]:
    """Every name the source imports, anywhere in it -> (module, attribute or "" for a module)."""
    out: dict[str, tuple[str, str]] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            out |= {a.asname or a.name: (n.module, a.name) for a in n.names}
        elif isinstance(n, ast.Import):
            for a in n.names:
                head = a.name.split(".")[0]
                out[a.asname or head] = (a.name if a.asname else head, "")
    return out


def _api(call: ast.Call, imported: dict[str, tuple[str, str]]) -> str | None:
    """The signal of the known API `call` reaches, if it reaches one (above)."""
    f = call.func
    if isinstance(f, ast.Name):
        module, attr = imported.get(f.id, ("", ""))
        if attr:
            return _MODULE_API.get(module, {}).get(attr) or _HOUSE_API.get(attr)
        return None if module else _HOUSE_API.get(f.id)
    if not isinstance(f, ast.Attribute):
        return None
    if f.attr in _HOUSE_API:
        return _HOUSE_API[f.attr]
    base = f.value
    if isinstance(base, ast.Name):
        module, attr = imported.get(base.id, (base.id if base.id in _MODULE_API else "", ""))
        if module and not attr:  # called on a module: its own API, or none
            return _MODULE_API.get(module, {}).get(f.attr)
    elif isinstance(base, ast.Attribute) and base.attr in _MODULE_API:  # `timing_bounds.time.x`
        return _MODULE_API[base.attr].get(f.attr)
    return _METHODS.get(f.attr)


def _bounds_time(fn: ast.AST, param: str) -> bool:
    """`param` bounds a wait in `fn`: a `timeout=` keyword, an argument to a wait, or arithmetic
    (`time.time() + timeout`) -- not data, such as a hook's `{"timeout": timeout}`."""
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            vals = [kw.value for kw in n.keywords if kw.arg == "timeout"]
            if _call_name(n)[0] in {*_WAITS, "GiveUp", "patience", "sleep"}:
                vals += n.args
            if any(isinstance(v, ast.Name) and v.id == param for v in vals):
                return True
        if isinstance(n, ast.BinOp) and any(
            isinstance(s, ast.Name) and s.id == param for s in (n.left, n.right)
        ):
            return True
    return False


def direct(
    fn: ast.AST, imported: dict[str, tuple[str, str]] | None = None
) -> tuple[set[str], set[str]]:
    """(signals the body shows itself, names it refers to) for one function; `imported` is its
    module's `origins`."""
    imported = origins(fn) if imported is None else imported
    sig: set[str] = set()
    names: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            name, base = _call_name(n)
            if api := _api(n, imported):
                sig.add(api)
            timed = any(kw.arg == "timeout" for kw in n.keywords)
            if name in _WAITS and (timed or _timed_positionally(n, name)):
                sig.add("wait")
            if name == "wait" and base and not n.args and not n.keywords:
                sig.add("wait")
            for kw in n.keywords:
                v = _literal(kw.value) if kw.arg == "timeout" else None
                if v is not None and v < TIGHT_S:
                    sig.add("tight")
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):  # `def _run(..., timeout=20)`
            a = n.args
            pairs = [*zip(a.args[len(a.args) - len(a.defaults) :], a.defaults, strict=True)]
            pairs += [(k, d) for k, d in zip(a.kwonlyargs, a.kw_defaults, strict=True) if d]
            for arg, default in pairs:
                v = _literal(default) if "timeout" in arg.arg else None
                if v is not None and v < TIGHT_S and _bounds_time(n, arg.arg):
                    sig.add("tight")
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and _DEADLINE.search(n.value):
            sig.add("deadline")
        elif isinstance(n, ast.Name):
            names.add(n.id)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            names.add(f"{n.value.id}.{n.attr}")
    return sig, names


def _is_mark(node: ast.AST) -> tuple[bool, str]:
    """(is `pytest.mark.load_sensitive[(...)]`, its reason or "")."""
    call = node if isinstance(node, ast.Call) else None
    target = call.func if call else node
    ok = (
        isinstance(target, ast.Attribute)
        and target.attr == MARK
        and isinstance(target.value, ast.Attribute)
        and target.value.attr == "mark"
    )
    reason = ""
    if ok and call:
        for kw in call.keywords:
            if kw.arg == "reason" and isinstance(kw.value, ast.Constant):
                reason = str(kw.value.value).strip()
    return ok, reason


@dataclass
class Module:
    funcs: dict[str, tuple[set[str], set[str], list[str]]] = field(default_factory=dict)
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)  # local -> (module, name)
    tests: list[str] = field(default_factory=list)  # "f" or "Class::f"
    marks: dict[str, str | None] = field(default_factory=dict)  # test -> reason ("" none)
    module_mark: str | None = None  # None: no module-level mark; else its reason


def parse(source: str) -> Module:
    tree = ast.parse(source)
    mod = Module()
    imported = origins(tree)

    def add(qual: str, node: ast.FunctionDef | ast.AsyncFunctionDef, owner: str) -> None:
        sig, names = direct(node, imported)
        if owner:  # self.helper inside a class is Class.helper
            names |= {f"{owner}.{n.split('.', 1)[1]}" for n in names if n.startswith("self.")}
        mod.funcs[qual] = (sig, names, [a.arg for a in node.args.args])
        if node.name.startswith("test_"):
            test = qual.replace(".", "::")
            mod.tests.append(test)
            for dec in node.decorator_list:
                hit, reason = _is_mark(dec)
                if hit:
                    mod.marks[test] = reason
            if owner and owner in class_marks:
                mod.marks.setdefault(test, class_marks[owner])

    class_marks: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(stmt.name, stmt, "")
        elif isinstance(stmt, ast.ClassDef):
            for dec in stmt.decorator_list:
                hit, reason = _is_mark(dec)
                if hit:
                    class_marks[stmt.name] = reason
            for m in stmt.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(f"{stmt.name}.{m.name}", m, stmt.name)
        elif isinstance(stmt, ast.ImportFrom) and stmt.module:
            for a in stmt.names:
                mod.imports[a.asname or a.name] = (stmt.module, a.name)
        elif isinstance(stmt, ast.Import):
            for a in stmt.names:
                mod.imports[a.asname or a.name] = (a.name, "")
        elif isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets
        ):
            values = (
                stmt.value.elts if isinstance(stmt.value, (ast.List, ast.Tuple)) else [stmt.value]
            )
            for v in values:
                hit, reason = _is_mark(v)
                if hit:
                    mod.module_mark = reason
    return mod


class Census:
    """Every module of tests/ parsed once; `signals(module, test)` follows what the test reaches."""

    def __init__(self, sources: dict[str, str]) -> None:
        self.mods = {name: parse(src) for name, src in sources.items()}
        self._memo: dict[tuple[str, str], frozenset[str]] = {}

    @classmethod
    def of(cls, tests: Path) -> Census:
        return cls({p.stem: p.read_text(encoding="utf-8") for p in sorted(tests.glob("*.py"))})

    def _resolve(self, mod: str, ref: str) -> tuple[str, str] | None:
        m = self.mods[mod]
        if ref in m.funcs:
            return mod, ref
        if ref in m.imports:
            src, name = m.imports[ref]
            return (src, name) if name and src in self.mods else None
        head, _, rest = ref.partition(".")
        if rest and head in m.imports and not m.imports[head][1] and head in self.mods:
            return head, rest
        if mod != "conftest" and "conftest" in self.mods and ref in self.mods["conftest"].funcs:
            return "conftest", ref
        return None

    def _signals(self, mod: str, qual: str, stack: frozenset) -> frozenset[str]:
        key = (mod, qual)
        if key in self._memo:
            return self._memo[key]
        if key in stack or mod not in self.mods or qual not in self.mods[mod].funcs:
            return frozenset()
        sig, names, args = self.mods[mod].funcs[qual]
        out = set(sig)
        for ref in sorted(names | set(args)):
            hit = self._resolve(mod, ref)
            if hit and hit != key:
                out |= self._signals(*hit, stack | {key})
        self._memo[key] = frozenset(out)
        return self._memo[key]

    def signals(self, mod: str, test: str) -> frozenset[str]:
        return self._signals(mod, test.replace("::", "."), frozenset())

    def derived(self) -> dict[str, list[str]]:
        """`tests/<module>.py::<test>` -> its signals, for every test that shows one."""
        out: dict[str, list[str]] = {}
        for name, m in self.mods.items():
            if not name.startswith("test_"):
                continue
            for test in m.tests:
                sig = self.signals(name, test)
                if sig:
                    out[f"tests/{name}.py::{test}"] = sorted(sig)
        return out

    def marked(self) -> dict[str, str]:
        """`tests/<module>.py::<test>` -> its mark's reason ("" for none), for every marked test."""
        out: dict[str, str] = {}
        for name, m in self.mods.items():
            if not name.startswith("test_"):
                continue
            for test in m.tests:
                reason = m.marks.get(test, m.module_mark)
                if reason is not None:
                    out[f"tests/{name}.py::{test}"] = reason
        return out
