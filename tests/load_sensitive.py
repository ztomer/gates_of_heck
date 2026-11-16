"""The tests that can fail under load and pass without it: the loaded run's set (GOH.LOADEDSUBSET).

The loaded run (tools/loaded_suite.sh, recorded per tree by lib/loaded_verdict.py) ran the whole
suite under 16 spinners, 470-570 s, and every ship after a land waited for it. A test whose verdict
cannot move with the box's load gains nothing from that run: the land gate already ran it. This
module DERIVES the tests whose verdict can move, so the loaded run runs only those
(`pytest -m load_sensitive`), and tests/test_load_sensitive.py refuses a test the derivation names
that does not carry the marker -- the set cannot rot as tests are added.

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

_CALLS = {
    "sleep": "sleep",
    "monotonic": "clock",
    "monotonic_ns": "clock",
    "perf_counter": "clock",
    "perf_counter_ns": "clock",
    "assert_sooner": "clock",
    "GiveUp": "poll",
    "Popen": "spawn",
    "fork": "spawn",
    "Thread": "thread",
    "Process": "thread",
    "Event": "thread",
    "Timer": "thread",
    "kill": "signal",
    "killpg": "signal",
    "send_signal": "signal",
    "terminate": "signal",
    "alarm": "signal",
}
_WAITS = {"wait", "join", "communicate", "get", "acquire"}
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


def direct(fn: ast.AST) -> tuple[set[str], set[str]]:
    """(signals the body shows itself, names it refers to) for one function."""
    sig: set[str] = set()
    names: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            name, base = _call_name(n)
            if name in _CALLS:
                sig.add(_CALLS[name])
            if base == "time" and name in ("time", "time_ns"):
                sig.add("clock")
            timed = any(kw.arg == "timeout" for kw in n.keywords)
            if name in _WAITS and (timed or any(_literal(a) is not None for a in n.args)):
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

    def add(qual: str, node: ast.FunctionDef | ast.AsyncFunctionDef, owner: str) -> None:
        sig, names = direct(node)
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
