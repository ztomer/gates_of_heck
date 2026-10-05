"""WHO PROTECTS THIS SPAWN: the attribution rules, and the shape questions they ask first.

    checks/_spawn_guard_attr.py            # nothing to run; import it

Split out of `_spawn_rust.py` at the 500-line cap, and the cut is at a real boundary: everything here
answers ONE question -- given a spawn, what protects it -- while `_spawn_rust.py` answers what
counts as a spawn, which function contains it, and where its reap is. Those are three questions and
they were crowding each other.

# THE INVARIANT: PROTECTION ATTACHES TO THE BINDING

A guard protects the VALUE it wraps. It used to protect the whole enclosing FUNCTION, and two
consumers found what that costs in the same week (2026-10-05):

  * `routines` -- `tests/control_lifecycle.rs:227`, `tests/control_takeover.rs:190`: a raw
    `/bin/sleep 300` owner beside a `Reap`-wrapped server, reaped below `wait_for_socket`'s `assert!`.
    A guard on `child` laundered `owner`. Both files read clean.
  * `monitor` -- `crates/multitop/src/ssh/ssh_tests.rs`: with the guard's `Drop` body emptied to
    nothing, the gate still reported clean, because `Self(` was accepted whenever ANY `impl Drop`
    existed in the file. A `Drop` that does nothing passed as protection.

So every branch below names the binding, or resolves `Self` to the type whose `Drop` was read. There
is no branch that accepts a guard because it appears nearby, because "nearby" is what the bug was.

# THE HONEST LIMITS, IN THE ORDER A READER MEETS THEM

`REPORT_GUARD` means "provably protected, and the proof is in this file or in the crate scope it was
given". Everything else is either a real finding or `unjudgeable` -- reported, counted, never failed
on, and named. The two ways to handle a shape a text pass cannot judge are both wrong: passing it
silently is the blind spot this module exists to remove, and failing it teaches everyone to ignore
the gate. The same reasoning as `is_locked`'s "an unanswerable question must not read as free".

# WHY `from __future__ import annotations` IS NOT OPTIONAL HERE

The `str | None` on `_foreign_guard` is evaluated at DEFINITION time unless that import is present,
and `install.sh` runs on whatever interpreter the PATH offers rather than this session's. Without
it the SELF-HOST install test failed with `TypeError: unsupported operand type(s) for |: 'type' and
'NoneType'` -- inside the one test whose subject is the install, where it reads as the install being
broken. Found by running `./tools/gate.sh --full`, not by reading.
"""

from __future__ import annotations

import re

from _spawn_lex import (
    EXTERNAL_GUARDS,
    EXTERNAL_REAPERS,
    KILLER,
    LET_BIND,
    RETURNS,
)

# The sentinel `attribute` returns for "protected, and provably so": a verdict, not a tag.
REPORT_GUARD = ("guard", "")


def _construction_carries(text, owners, binding):
    """Does a construction of one of `owners` in `text` carry THIS binding into it?

    Multi-line on purpose. The three measured spellings are `Reap(child)` on one line,
    `Server { child, .. }` across four, and `Self { child }` in a factory that bound it earlier --
    and a line-bounded match reads only the first, which reported `routines`' `mcp_stdio` harness
    (a `Server` whose `Drop` closes stdin, polls, then kills and waits) as a live orphan.
    `[^{};]` keeps the search inside one construction, so a guard mention in a neighbouring
    statement cannot reach across and claim this binding.
    """
    if binding is None or not text:
        return False
    for name in owners:
        pat = (
            rf"\b{re.escape(name)}\s*(?:::|\s*[\{{\(])"
            rf"(?:[^;{{}}]|\n){{0,240}}?\b{re.escape(binding)}\b"
        )
        if re.search(pat, text):
            return True
    return False


def _wraps(lines, end, close, binding, guards):
    """A local guard CONSTRUCTED FROM the binding on a later line: `let s = Reap(child);`.

    The measured row "a guard built from the handle on the NEXT line". The window is the rest of the
    enclosing function, NOT a fixed number of lines: the incident's own repaired file puts eight
    lines of comment explaining WHY the guard exists between the spawn and
    `let mut child = ReapOnDrop(child);`, and a line window stopped one short and reported the
    incident's fix as a live orphan. Scoping to the function is what makes the window correct rather
    than lucky, and the guard still has to NAME the binding -- a `Reap(some_other_child)` elsewhere
    in the function is a different guard on a different value.
    """
    return _construction_carries("\n".join(lines[end + 1 : close + 1]), guards, binding)


def _foreign_guard(head_lines, at, guards, known_types) -> str | None:
    """A type the spawn's OWN CONSTRUCTOR chain names that this file does not define, or None.

    Scoped to the chain: a `Command` builder is `Command::new(..).args(..).stdout(Stdio::piped())`,
    and every hop in it is a CONSTRUCTOR CALL on the way to `.spawn()`. A guard is very often defined
    once in `tests/common/mod.rs` and used by every test file in the crate, so a per-file reading
    cannot see whether its `Drop` reaps -- and both ways to respond are wrong: passing it silently
    (the blind spot this release is about) or failing it (flagging correct code, which is how a gate
    gets switched off). It is REPORTED as `unjudgeable`, counted, and never failed on.

    It must be the CHAIN, not the statement. Read as the statement, `Stdio::piped()` -- an argument
    two hops below `Command::new` -- was reported as an unjudgeable wrapper, which silenced the
    incident's own test: the finding it exists to assert stopped being a finding. A constructor in
    the chain is only evidence if it CONSUMES the spawn, and the one that does is the outermost one
    the chain began with.

    `Command` is the builder the chain is BUILT with, not a wrapper AROUND the spawn, so it is never
    one: the first version did not exclude it and reported `Command::new(..).spawn()` -- every
    process spawn there is -- as an unjudgeable wrapper, which silenced four table rows and the
    reported guard rows at the same time. A gate that cannot report the incident is worse than one
    that reports too much, and this is the third time that has been true in this checker.
    """
    first = next((j for j in range(at - 1, -1, -1) if LET_BIND.search(head_lines[j])), None)
    if first is None:
        return None
    head = "\n".join(head_lines[first : at + 1])
    # `let NAME = <Type>(` -- the wrapper the chain is an argument to, if there is one.
    m = re.search(
        r"\blet\s+(?:mut\s+)?\w+\s*(?::[^=;\n]+)?=\s*([A-Z][A-Za-z0-9_]*)\s*(?:::|\()", head
    )
    name = m.group(1) if m else None
    if (
        name is None
        or name == "Command"
        or name in guards
        or name in known_types
        or name in EXTERNAL_GUARDS
    ):
        return None
    return name


def _watchdog_signals(masked: str, binding: str, watchdog_bodies) -> bool:
    """Does a watchdog in this function actually signal THIS child's pid?

    The watchdog signals a pid, and the pid comes from `<binding>.id()`. Function-scoped, a watchdog
    armed for one child bounded the leak of another -- the same laundering as the guard, and it was
    caught by a probe row rather than by the estate, which is the honest way to find it.

    The search is inside the watchdog's OWN closure, not the function body: an `assert!(owner.id() > 0)`
    below the spawn mentions the binding, and reading that as "the watchdog acts on this child" let a
    watchdog aimed at a different pid vouch for it.
    """
    derived = re.findall(
        rf"\b(\w+)\s*(?::[^=;\n]+)?=\s*[^\n;]*\b{re.escape(binding)}\s*\.\s*"
        r"(?:id|get\s*\(\s*0)\s*\(",
        masked,
    )
    for text in watchdog_bodies:
        if not KILLER.search(text):
            continue
        if re.search(rf"\b{re.escape(binding)}\b", text):
            return True
        if any(re.search(rf"\b{re.escape(n)}\b", text) for n in derived):
            return True
    return False


def attribute(
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
    self_type,
    watchdog_bodies,
):
    """Why THIS spawn cannot leak, or None. Every branch names the binding, or names nothing.

    The branches are ordered so the strongest evidence is spent first: a guard type in the spawn's
    OWN statement, then the handle moved into a guard, then a factory whose `Self` resolves to a
    guard whose `Drop` was read, then a reaper or a watchdog that acts on this binding.

    A guard belonging to a DIFFERENT variable produces none of them. That is the fix.
    """
    if any(g in statement for g in EXTERNAL_GUARDS):
        return REPORT_GUARD
    if guards and any(re.search(rf"\b{re.escape(g)}\b", statement) for g in guards):
        return REPORT_GUARD
    if _wraps(lines, end, close, binding, guards):
        return REPORT_GUARD
    # A factory: the Child reaches `Self` and the type it names has to be read. Two spellings, both
    # measured -- `Self(command.spawn().unwrap())`, where the Child is wrapped by a TUPLE FIELD with
    # no binding at all (`monitor`), and `Self { child }` further down the body of a factory that
    # bound it first (`Holder::open`). `Self` alone is not evidence, which is the whole of bug 3.
    if re.search(r"\bSelf\s*[\{\(]", statement) or _construction_carries(body, ("Self",), binding):
        if self_type and self_type in guards:
            return REPORT_GUARD
        if self_type is None:
            return (
                "unjudgeable",
                "the Child is handed to `Self`, but the enclosing block has no resolvable `impl`, "
                "so this pass cannot read the `Drop` that would reap it",
            )
        if self_type in drops:
            return (
                "unreaped-spawn",
                f"wrapped in `{self_type}`, whose `Drop` neither kills nor waits: the type claims "
                "to clean up on unwind and does not, so a panic leaves a LIVE ORPHAN",
            )
        # A type with NO `Drop` at all: the reap really is the caller's, and `-> Self` is the
        # evidence that it left this function. Reading it as a leak would flag every `fn open() ->
        # Self` that lets the caller reap, which is correct Rust.
        if RETURNS.search(lines[sig]):
            return (
                "handoff",
                f"the Child is handed to `{self_type}`, which has no `Drop`; the caller owes the reap",
            )
    for name in EXTERNAL_REAPERS:
        if binding and re.search(rf"\b{re.escape(name)}\s*\([^;\n]*\b{re.escape(binding)}\b", body):
            return REPORT_GUARD
    if (
        binding
        and watchdog_bodies
        and _watchdog_signals("\n".join(lines[sig : close + 1]), binding, watchdog_bodies)
    ):
        return (
            "watchdog",
            "a background thread kills this child after a deadline: the leak is bounded, "
            "not prevented — a ReapOnDrop guard is the shape that prevents it",
        )
    foreign = _foreign_guard(lines, head, guards, known)
    if foreign:
        return (
            "unjudgeable",
            f"the Child is wrapped in `{foreign}(…)`, a type this file does not define — its "
            "`Drop` is somewhere else, so whether it reaps cannot be judged from here",
        )
    return None
