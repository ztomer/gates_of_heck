"""The RUST rules: WHERE a spawn is, which function holds it, and where its reap is.

    checks/_spawn_rust.py            # nothing to run; import it

Split out of `_spawn_shapes.py` at the 500-line cap, and again when the binding rules arrived. The
companions are `_spawn_lex.py` (the shared VOCABULARY -- duplicated patterns drift, and two files that
disagree about what a guard is disagree about the estate), `_spawn_guard_attr.py` (WHAT PROTECTS A
SPAWN) and `_spawn_py_sh.py` (the Python and shell rules).

Everything here takes ALREADY-MASKED Rust text and knows nothing about files, git, or what a test is.

THE MEASURED TABLE behind these rules is in `check_no_unreaped_spawn.py`'s docstring, re-asserted row
by row by `_unreaped_spawn_probe.py`. Do not widen or narrow a rule here without the probe going red:
the table is the specification, and a docstring is not.

# WHAT THIS FILE OWNS, and the sibling bugs found while fixing them

Protection attaches to the BINDING (rules in `_spawn_guard_attr.py`); what is left here locates the
spawn, and each of these read a shape it could not actually see.

  * `is_command_spawn` -- `Command::new` had to be within eight lines of the `.spawn()`, so
    `cmd.spawn()` -- where `cmd` is a `&mut Command` parameter, which is how a guard's own constructor
    spawns -- was not recognised as a process spawn AT ALL. Measured over the estate's test sources:
    19 `.spawn(` on a bare identifier, `cmd`/`command` (10, process spawns) and `scope`/`handle`/`s`
    (9, rayon / tokio / thread handles). Only the receiver's DECLARED TYPE separates them.
  * `_fn_bounds` -- the nearest preceding `fn` is not always the enclosing one. A guard declared
    INSIDE a test has an `impl Drop for Guard { fn drop(&mut self) { … } }` above the spawn, and
    `routines`' `tests/control_socket.rs:277` was read as living inside that `fn drop`, twelve lines
    above the spawn. Every rule downstream is scoped to the enclosing function, so a function that
    does not contain the spawn makes all of them read nothing.
  * `_stmt_head` / `_binding` -- a statement begins at its `let`, not at the spawn; a guard often
    wraps the spawn from ABOVE, and a window starting at `.spawn()` cannot see it.
  * `panicking_callees` -- a call to a same-FILE function whose body can panic is panicking:
    `wait_for_socket` is a local function whose whole body is an `assert!`, and it was the only
    construct between `routines`' spawn and its reap. `_watchdog_bodies` looks for the signal inside
    the watchdog's OWN closure, since `assert!(owner.id() > 0)` names the binding and bounds nothing.

# WHY `from __future__ import annotations` IS NOT OPTIONAL HERE

`rust_findings`'s `str | None` is evaluated at DEFINITION time without it, and `install.sh` runs on
whatever interpreter the PATH offers rather than this session's. Without it the self-host install
test failed with `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'` -- inside the
one test whose subject is the install, where it reads as the install being broken. Found by running
`./tools/gate.sh --full`; the FIRST fix put the import in the wrong module of the two, which is how
a per-instance patch is distinguishable from a class fix. The class is "every new module under
`checks/` that annotates with PEP 604" -- `docs/BACKLOG.md` records that sweep.
"""

from __future__ import annotations

import re

from _spawn_guard_attr import REPORT_GUARD, attribute
from _spawn_guard_attr_rust import (
    _binding,
    _known_types,
    _self_type,
    _stmt_head,
    rust_region,
)
from _spawn_reap import reap_line
from _spawn_lex import (
    CMD_DECL,
    CMD_NEW,
    DROP_IMPL,
    FN,
    IMPL_TYPE,
    KILLER,
    LET_BIND,
    PANICS,
    RECEIVER,
    RETURNS,
    SPAWN,
    WATCHDOG,
)

# THE MEMO. One slot per question, keyed on the EXACT text it was derived from, so a different
# string is a miss rather than a wrong answer. Kept here rather than in a general-purpose cache
# because its soundness argument is local and checkable: every value in it is a pure function of its
# key, and a scan has one crate context. `tests/test_check_no_unreaped_spawn_scope.py` proves both
# halves -- that a second call is served from here, and that changing the text re-derives.
_MEMO: dict = {}


def guard_types(masked: str) -> set[str]:
    """Local types whose `Drop` impl STOPS the child: `kill()` **or** `wait()`.

    Judged by what the impl DOES, and this is where monitor's finding lived: the `Drop` body of its
    guard was emptied to nothing and the checker still called the spawn guarded, because the
    `Self(` escape hatch in `rust_findings` licensed any construction whenever *some* `impl Drop`
    existed in the file. Reading the body here is not sufficient on its own -- `_self_type` has to
    resolve which type the `Self` belongs to, and this function still has to recognise that type.

    `kill` AND `wait` was the first rule and it was WRONG, found by the estate sweep:
    `mediaops-rs`' `impl Drop for Holder { drop(self.child.stdin.take()); let _ = self.child.wait(); }`
    closes stdin to make `sqlite3` exit, and a kill-AND-wait rule called that a leak. `wait()` alone in
    a `Drop` IS the reap; requiring `kill` as well flags correct code.

    MEMOISED, and that is a correctness-of-cost fact rather than a trick: `rust_findings` is called
    once per file over a crate context that is the same string every time, so an unmemoised read
    rescanned the whole crate once per file. Measured over `media_server`'s `crates/` tree, 2026-10-05:
    341 files against a 2.77 MB crate context, and a per-file `rust_findings` with the context cost
    **134x** one without it. The memo is keyed on the exact string, so a different crate cannot read
    another's answer -- and it is a SINGLE slot, because a scan has exactly one crate context and a
    wider cache would only retain megabytes.
    """
    hit = _MEMO.get("guard")
    if hit is not None and hit[0] == masked:
        return hit[1]
    found = _guard_types(masked)
    _MEMO["guard"] = (masked, found)
    return found


def _guard_types(masked: str) -> set[str]:
    """`guard_types`' body. Split out so the memo is visible as a wrapper, not buried in a loop."""
    found = set()
    for m in DROP_IMPL.finditer(masked):
        depth, i = 0, m.end() - 1
        while i < len(masked):
            if masked[i] == "{":
                depth += 1
            elif masked[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = masked[m.end() : i]
        if re.search(
            r"\.\s*(?:kill|wait|wait_with_output|wait_timeout|try_wait|terminate)\s*\(", body
        ):
            found.add(m.group(1))
    return found


def _statement_end(lines, start, close, depth0):
    """(last line of the statement that begins at `start`, depth-relative `;` seen?).

    `depth0` is the brace depth AT the spawn, not at the line start: the spawn is usually the third
    line of a chained `let`, already several parens deep. Anchored at the line start it terminated
    early on a nested expression and handed the next lines to the panic scan as if they were part of
    the statement, which is how a guard two lines below a `let` stopped counting as a guard.
    """
    depth = 0
    for idx in range(start, min(start + 40, close + 1)):
        for ch in lines[idx]:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
        if depth <= depth0:
            tail = lines[idx].rstrip()
            if tail.endswith((";", ",")) or not tail.strip():
                return idx, tail.endswith(";")
            if idx > start and re.match(r"^\s*(return\b|})", lines[idx]):
                return idx, False
    return min(start + 39, close), False


def _depth_at(lines, sig, upto):
    """Brace depth at the START of line `upto`, counted from the function signature line."""
    depth = 0
    for j in range(sig, upto):
        depth += lines[j].count("{") - lines[j].count("}")
    return depth


def _fn_bounds(lines, at):
    """(signature line, closing-brace line) for the function containing line `at`.

    Brace depth, not indentation: rustfmt indents a nested `fn` inside a `mod`. THE FUNCTION MUST
    CONTAIN `at`. The nearest preceding `fn` is not always the enclosing one: a
    guard declared INSIDE a test has an `impl Drop for Guard { fn drop(&mut self) { … } }` above
    the spawn, and `routines`' `tests/control_socket.rs:277` was read as living inside that
    `fn drop`, whose body ended twelve lines ABOVE the spawn. Every downstream rule is scoped to the
    enclosing function -- the reap window, the guard window, the watchdog -- so a function that does
    not contain the spawn makes all of them read nothing and turns a correctly guarded server into a
    reported orphan. A candidate whose body does not reach `at` is skipped and the search continues
    upwards.
    """
    depth = 0
    fallback = None
    for i in range(at, -1, -1):
        depth += lines[i].count("}") - lines[i].count("{")
        if depth > 0:
            continue
        sig_from = i
        while sig_from >= 0 and not FN.match(lines[sig_from]):
            sig_from -= 1
        if sig_from < 0:
            break
        fallback = fallback if fallback is not None else sig_from
        d = 0
        for j in range(sig_from, len(lines)):
            d += lines[j].count("{") - lines[j].count("}")
            if d <= 0 and j >= sig_from:
                if sig_from <= at <= j:
                    return sig_from, j
                break
    return (fallback if fallback is not None else 0), len(lines) - 1


def _fn_returns(lines, sig_line):
    depth = 0
    for i in range(sig_line, len(lines)):
        line = lines[i]
        if FN.match(line) and RETURNS.search(line):
            return True
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            return False
    return False


# A deadline WATCHDOG: a background thread that SIGKILLs this child's pid after N seconds. Found on
# `media_server/crates/lan-ip-watcher-rs/tests/test_cli.rs`, which spawns the watcher daemon,
# records `child.id()`, and arms `thread::spawn(move || { sleep(DEADLINE); libc::kill(pid,
# SIGKILL) })`. It is not a guard -- the child really does outlive the test -- but the leak is BOUNDED
# by the deadline, so it cannot outlive the suite, and a gate that failed it would be failing the
# second-best answer to this defect. Accepted, and COUNTED in the pass line, because a mitigation
# nobody can see is a mitigation that decays.


def _watchdog_bodies(scope_text: str) -> list[str]:
    """The source of each `thread::spawn(...)` closure inside one function.

    Brace-matched from the `(` after the call, so the search is over the CLOSURE and not over the
    function: `libc::kill(pid, ..)` three lines below the spawn is not the same statement as
    `assert!(owner.id() > 0)` that merely mentions the binding.
    """
    if not WATCHDOG.search(scope_text):
        return []
    out = []
    for m in WATCHDOG.finditer(scope_text):
        i = scope_text.find("(", m.end())
        if i < 0:
            continue
        depth, start = 0, None
        while i < len(scope_text):
            if scope_text[i] in "([{":
                depth += 1
                if depth == 1:
                    start = i + 1
            elif scope_text[i] in ")]}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if start is not None:
            out.append(scope_text[start:i])
    return out


def drop_types(masked: str) -> set[str]:
    """Every type with an `impl Drop`, whether or not its `Drop` reaps.

    The distinction `guard_types` alone cannot express, and it is the one `monitor` found: a type
    that HAS a `Drop` which does not kill or wait is not a handoff to a caller who will reap it, it
    is a type that claims to clean up and does not. Reading the body says which; this says a body
    existed to read.

    Memoised for the same reason and with the same single-slot discipline as `guard_types`: it is
    called four times per file, twice over the crate context, and the crate context does not change
    within a scan.
    """
    hit = _MEMO.get("drop")
    if hit is not None and hit[0] == masked:
        return hit[1]
    found = {m.group(1) for m in DROP_IMPL.finditer(masked)}
    _MEMO["drop"] = (masked, found)
    return found


def crate_facts(crate: str):
    """The three crate-scope sets, derived ONCE per crate instead of once per file.

    `guard_types(crate)`, `drop_types(crate)` and `_known_types(crate)` are pure functions of
    `crate`, and `crate` is one string for the whole scan -- so deriving them per file is a
    quadratic with the corpus size as one factor and the number of test files as the other. That is
    what made the R3 estate sweep cost 23 s a pass over `media_server`'s 626-file `crates/` tree
    (measured 2026-10-05) and put this repo's own suite at 234 s: the same answer, recomputed 341
    times.

    A single-slot memo keyed on the exact string. Not a cache with a TTL and not a cache that can
    report a stale "clean": a DIFFERENT crate text misses and is derived, and equal text means the
    answer is the answer. `clear_memo` exists so a test can prove the derivation still runs.
    """
    hit = _MEMO.get("crate")
    if hit is not None and hit[0] == crate:
        return hit[1]
    facts = (guard_types(crate), drop_types(crate), _known_types(crate))
    _MEMO["crate"] = (crate, facts)
    return facts


def clear_memo() -> None:
    """Drop every memoised answer. For the test that proves the derivations still happen."""
    _MEMO.clear()


def _all_fns(lines):
    """[(name, signature line, closing-brace line)] for every `fn` defined in this file."""
    out = []
    depth = 0
    for i, line in enumerate(lines):
        if depth <= 0:
            m = FN.match(line)
            if m:
                sig = i
                while sig < len(lines) and "{" not in lines[sig]:
                    if FN.search(lines[sig]) and sig != i:
                        break
                    sig += 1
                if sig >= len(lines):
                    continue
                d = 0
                for j in range(sig, len(lines)):
                    d += lines[j].count("{") - lines[j].count("}")
                    if d <= 0 and j >= sig:
                        out.append((m.group(1), sig, j))
                        break
        depth += line.count("{") - line.count("}")
    return out


def panicking_callees(masked: str) -> set[str]:
    """Names of functions DEFINED IN THIS FILE whose body can panic.

    The panic scan was line-local, so a call to a helper that asserts read as inert -- and the
    incident shape is exactly that: `routines`' `tests/control_lifecycle.rs:227` reaps the raw owner
    at line 245, below `wait_for_socket(&sock)` at line 243, whose whole body is an `assert!`. With
    the guard laundering fixed the owner was STILL read clean, because the only construct between
    the spawn and the reap was a call. Resolving the callee is not the interprocedural hole the
    checker's docstring names: that is about a spawn RETURNED to a caller, which changes who OWNS the
    reap. This is about a panic crossing a call boundary inside ONE file, which is what skipped the
    reap. Same-file only, deliberately: a helper in another crate is an unknown contract, and
    treating an unknown as panicking would flag every reap in the estate.
    """
    lines = masked.split("\n")
    out = set()
    for name, sig, close in _all_fns(lines):
        if any(PANICS.search(lines[j]) for j in range(sig, min(close + 1, len(lines)))):
            out.add(name)
    return out


def _calls_callee(line: str, callees: set[str]) -> bool:
    """Does `line` CALL one of the panicking callees (not merely mention it)?"""
    return any(re.search(rf"(?<![\w:.]){re.escape(name)}\s*\(", line) for name in callees)


def _ordering_finding(lines, end, reap, callees=()):
    """The `reap-after-panic` finding, or None.

    A SEPARATE named function, because this rule is the one the whole gate exists for and a test
    has to be able to switch it off to prove the probe notices.
    """
    early = [
        j + 1
        for j in range(end + 1, reap)
        if PANICS.search(lines[j]) or _calls_callee(lines[j], callees)
    ]
    if not early:
        return None
    return (
        f"the reap is at line {reap + 1}, AFTER a construct that can panic at line {early[0]} — a "
        "failing test skips the reap and leaks a server that never exits"
    )


def is_command_spawn(lines, i):
    """Is the `.spawn()` on line `i` a PROCESS spawn?

    `Command::new` is almost never on the same line as `.spawn()` -- rustfmt puts the chain out over
    four lines, which is what the incident's file looked like:

        let mut child = Command::new(env!("CARGO_BIN_EXE_archive_torznab"))
            .args(["--bind", "127.0.0.1:0"])
            .stderr(Stdio::piped())
            .spawn()

    so the receiver is found by looking BACK to the nearest statement boundary (`;`, `{`, `}`) and
    asking whether `Command::new` is in the window. The first version of this function matched
    `CMD_NEW` on the spawn line only, and reported the incident CLEAN -- a line-local rule missing
    the shape every real call site has.

    THE SECOND FORM is a receiver already typed as a `Command`, which is how a guard's own
    constructor spawns: `fn spawn(command: &mut std::process::Command) -> Self`. `CMD_NEW` is not
    within eight lines of that `.spawn()` -- the `Command` arrived as a PARAMETER -- so the whole
    call was invisible, and monitor's guard was invisible with it. Keyed on the declared type
    because the estate also has `scope.spawn`, `handle.spawn` and `s.spawn` (rayon, tokio, a plain
    thread handle) on a bare identifier, and a name list would have to know all of them.
    """
    floor = max(0, i - 8)
    for j in range(i, floor - 1, -1):
        if CMD_NEW.search(lines[j]):
            return True
        if j < i and re.search(r"[;{}]", lines[j]):
            break
    m = RECEIVER.search(lines[i])
    if not m:
        return False
    name = m.group(1)
    decl = re.compile(CMD_DECL.pattern.format(name=re.escape(name)))
    return any(decl.search(line) for line in lines if name in line) or bool(
        decl.search("\n".join(lines[max(0, i - 40) : i + 40]))
    )


def rust_findings(masked: str, crate: str | None = None) -> list[tuple[int, str, str]]:
    """[(lineno, tag, why)] over one masked Rust file.

    The guard set is derived HERE rather than taken as an argument: it is the one input a caller
    gets wrong silently, and a probe that passed `set()` would report every guarded spawn as a leak
    -- which is the failure mode of a gate whose blind spot is its own interface.

    `crate` is the masked concatenation of every Rust file in the same scope, and it exists because
    a guard is very often defined ONCE in `tests/common/` and used by every test file in the crate.
    `routines` does exactly this: `ReapOnDrop` lives in `tests/lifecycle_support/mod.rs` and three
    test files wrap their children in it. Read per file, `guard_types` cannot see it, so the two
    sites that were FIXED by that guard reported as `unjudgeable` -- a false alarm on code that is
    correct, on the same two files whose leak this whole release exists to catch. Gathering the
    guard types at crate scope is the difference between "this gate says something here" and
    "this gate says something TRUE here".

    A CONSTRUCTION the crate scope recognises is trusted as a guard only if it is not contradicted
    locally: a type with a local `impl Drop` that does not reap stays a finding, because the local
    definition is the one the compiler uses and a crate-scope sighting of the same name is a
    different type.

    The three crate-scope sets come from `crate_facts`, so they are derived once for the crate
    rather than once per file. The ARITHMETIC here is deliberately unchanged -- same sets, same
    subtractions, same unions -- because this is a cost fix and a rule that moves with it would be
    two changes wearing one commit.
    """
    crate_guards, crate_drops, crate_known = crate_facts(crate) if crate else (set(), set(), set())
    local_guards = guard_types(masked)
    local_drops = drop_types(masked)
    guards = local_guards
    if crate:
        guards = guards | (crate_guards - local_drops)
    drops = local_drops
    if crate:
        drops = drops | (crate_drops - local_guards)
    known = _known_types(masked) | crate_known
    callees = panicking_callees(masked)
    lines = masked.split("\n")
    out: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        if not SPAWN.search(line) or not is_command_spawn(lines, i):
            continue
        sig, close = _fn_bounds(lines, i)
        end, semicolon = _statement_end(lines, i, close, _depth_at(lines, sig, i))
        head = _stmt_head(lines, i, sig)
        statement = "\n".join(lines[head : end + 1])
        body = "\n".join(lines[sig : close + 1])
        binding = _binding(lines, i, sig)
        watchdogs = _watchdog_bodies("\n".join(lines[sig : close + 1]))
        tag = attribute(
            statement,
            body,
            lines,
            head,
            end,
            close,
            sig,
            binding,
            guards,
            known,
            drops,
            _self_type(lines, sig),
            watchdogs,
        )
        if tag is not None:
            if tag is not REPORT_GUARD:
                out.append((i + 1, tag[0], tag[1]))
            continue
        if re.search(r"^\s*return\b", statement) or (not semicolon and _fn_returns(lines, sig)):
            out.append(
                (i + 1, "handoff", "spawn handed to the caller; this gate does not follow it")
            )
            continue
        reap = reap_line(lines, end + 1, close)
        if reap is None:
            out.append(
                (
                    i + 1,
                    "unreaped-spawn",
                    "a Child that is never kill()ed or wait()ed: measured LIVE ORPHAN",
                )
            )
            continue
        early = _ordering_finding(lines, end, reap, callees)
        if early:
            out.append((i + 1, "reap-after-panic", early))
    return out
