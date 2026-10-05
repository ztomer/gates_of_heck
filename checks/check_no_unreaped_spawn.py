#!/usr/bin/env python3
"""Fail if a TEST spawns a long-running child that no guard reaps -- and fail if the reap is real
but sits BELOW an assertion that can panic.

    python3 checks/check_no_unreaped_spawn.py                    # all tracked test sources (CI)
    python3 checks/check_no_unreaped_spawn.py --staged           # only staged files (pre-commit)
    python3 checks/check_no_unreaped_spawn.py --exclude '^vendor/'
    python3 checks/check_no_unreaped_spawn.py --probe            # prove this gate can go red

# WHY, in the shape it cost

`media_server/crates/archive-torznab-rs/tests/test_net_and_bin.rs` spawned the real binary with
`--bind 127.0.0.1:0` -- a server that loops forever BY DESIGN -- and reaped it with an explicit
`child.kill(); child.wait();` placed after the retry loop. Four separate things followed from one
line of placement, and the fourth is why it cost a day:

  * every assertion failure below the pair leaked a server that can never exit;
  * an interrupted run (a timeout, a cancelled agent) skipped the pair entirely;
  * each orphan held the cargo build lock, so every LATER `cargo test` blocked;
  * **the hang was the leak's SYMPTOM and the leak was invisible** -- the test process had been
    killed before it could report anything, so the only observable was silence. Nine orphans
    accumulated. Run to completion the suite takes 0.22 s.

So a plain "does this test kill its child" question is the wrong question. The reap EXISTED. What
made it a leak was that a panic -- the ordinary outcome of a failing test -- skips it. That is the
shape this gate is for, and it is checked FIRST, because a checker that only asks "is there a kill
somewhere in the function" reports this file clean.

# THE MEASURED TABLE (R2). Nothing below is read from documentation.

Measured on rustc 1.99.0 / macOS 27.0.1, 2026-10-03, by a PID-liveness probe: the child's pid is
read from the `Child` handle and its state letter read with `ps -o stat= -p <pid>`. The FIRST
version of that probe matched processes by NAME, and reported a confident `survivors=0` for every
arm -- because a copied `/bin/sleep` is killed by the kernel at exec (rc=137, its signature did not
travel with the copy), so the instrument was measuring a process that never existed. It is spelled
out because a measurement that cannot produce the wrong answer has not been shown to produce the
right one.

| shape                                                     | measured                       | this gate |
|------------------------------------------------------------|--------------------------------|-----------|
| `spawn()` then the `Child` is dropped                      | **LIVE orphan** (pid reparented to 1) | FINDING |
| `spawn()` then `kill()` with no `wait()`                   | **ZOMBIE** (killed, never reaped)     | clean |
| `spawn()` then `kill()` + `wait()`                          | reaped                               | clean |
| `spawn()` then `wait()` / `wait_with_output()`              | reaped                               | clean |
| `spawn()` then a `Drop` impl that kills and waits          | reaped                               | clean |
| ...the same guard, then a PANIC below the spawn            | **reaped** -- `Drop` runs on unwind   | clean |
| a raw `Child` then a PANIC below the spawn                 | **LIVE orphan**  <- THE INCIDENT      | FINDING |
| a raw `Child` BESIDE a guarded one, reap below an assert   | **LIVE orphan**  <- `routines`        | FINDING |
| a `Drop` that does nothing, `Self(` into it               | **LIVE orphan**  <- `monitor`         | FINDING |
| `Command::output()`                                        | reaped, but **BLOCKS** until the child exits | clean here |
| `Command::status()`                                        | same as `output()`                   | clean here |

The three rows added on 2026-10-05 were each measured reading CLEAN by the version of this gate
that shipped in v0.17.1, against the real file from the repo that found them. They are not new rules
argued from a principle; they are three shapes that turned out to be invisible and are now not.

The last two rows are measured DECISIONS, not oversights. `output()`/`status()` cannot leak -- they
wait for the child -- so flagging them would be flagging the fix. What they do instead is block
forever on a long-running child with no timeout of their own (measured: still running at 5 s against
a `sleep 300`), which is a DIFFERENT defect with a different home: a bounded wait belongs to the
gate runner, not to a static scan. `checks/` refuses that as "not applicable here"; see
`lib/bounded_run.py` and the ceiling in `gates/_common.sh`.

`kill()` WITHOUT `wait()` is deliberately NOT a finding, and it is the row a reader is most likely
to argue with. The measurement says a bare `kill()` leaves a ZOMBIE, and a zombie cannot hold a build
lock, cannot outlive the run, and cannot make a suite hang -- it is collected when the parent exits.
`kill` STOPS the child; `wait` only COLLECTS it. Only the first ends the leak, so only the first is
this gate's business, and flagging the second would be flagging correct Rust -- which is how a gate
earns being switched off. (The estate sweep is what forced this: four of the first seven hits across
twelve repos were correct code, and a checker that flags the fix is not a checker.)

# THE DROP GUARD, named because an unnamed pattern cannot be checked

**ReapOnDrop** -- a type holding the `Child` whose `Drop` impl calls `kill()` and `wait()`. It is
the only shape that survives a panic, because unwinding runs `Drop` on the way out; the raw handle
does not. This gate RECOGNISES any local type whose `Drop` impl does both, plus the two crates
that ship one under the same contract (`assert_cmd::Command`, `escargot::Command`), and reports a
raw `Child` bound in a test with neither.

Rust's spelling, from the file the incident was fixed in:

    struct ReapOnDrop(std::process::Child);
    impl Drop for ReapOnDrop {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

The same contract in the other two languages this gate reads: a Python `with subprocess.Popen(...)`
(`Popen.__exit__` closes the pipes and `wait()`s), and a shell `trap cleanup EXIT` where `cleanup`
kills the recorded `$!`. A guard is judged by what its `Drop`/`__exit__`/trap DOES, never by its
name -- a rename is not a repair, and the 2026-10-03 fix to the second defect in that same file was
already a rename.

# THE UNIT IS THE BINDING (2026-10-05)

Protection attaches to the VALUE, not to the function it was written in. It used to attach to the
function, and two consumers found the consequence in the same week:

* `routines` (`tests/control_lifecycle.rs:227`, `tests/control_takeover.rs:190`, fixed in v0.59.1) --
  a raw `/bin/sleep 300` owner beside a `Reap`-wrapped server, reaped below `wait_for_socket`'s
  `assert!`. A guard on `child` laundered `owner`, and both files read clean.
* `monitor` (`crates/multitop/src/ssh/ssh_tests.rs`, fixed in v0.52.0) -- with the guard's `Drop`
  body emptied to nothing, the gate still reported clean. `Self(` was accepted whenever ANY
  `impl Drop` existed in the file, so a `Drop` that does nothing passed as protection. That is
  precisely the failure `monitor`'s own test was written to catch, and the gate could not catch it.

The rule is now: resolve the binding the spawn's result was assigned to, and require the guard to
name THAT binding. A wrapper's ownership is often a `Self(...)` tuple field, so `Self` is resolved
through the enclosing `impl` and THAT type's `Drop` is read. The same attribution is applied to
every other protection -- an external reaper, a deadline watchdog -- because each one was laundering
a second child the same way. Every new rule has a row in `_unreaped_spawn_table_regressions.py` that
was measured CLEAN before the fix and FINDING after it, which is the only evidence that
distinguishes a fix from a rule change.

## THE MASKER, and why a bug in it is worse than no gate

The Rust masker desynchronised on a raw string: the closing delimiter was matched against the whole
OPENING token (`r#"`, `r##"##`), so `r#"{"op":"shutdown"}"#` never closed and every remaining line
of the file was blanked before the spawn scan saw it. **Nine spawn sites in `routines`' tests were
invisible to this gate**, which is why both files above read clean twice over.

Measured over the estate afterwards: sites policed rose 27 -> 30, all of them in `routines` and
`monitor`, and every one of the three is a shape the gate now judges correctly. The three valid
literal forms that desynchronised are each a table row, and each was verified against
`rustc 1.99.0 --edition 2024` rather than reasoned about -- `r"a\"b"` is not even valid Rust, so the
fixture that looked like a fifth case was the checker's own bug and the compiler said so.

## WHAT THIS IS NOT, and the blind spots are measured rather than hoped for

* **Per-function, not interprocedural.** A spawn RETURNED to the caller is a HANDOFF: reported and
  counted, never a finding, because the caller owes the reap and this checker does not follow the
  call. That is the largest hole and it is deliberate -- following it would mean deciding that a
  callee's contract is safe, which is the caller's judgement, not a text pass's. **Changed in
  2026-10-05 in ONE respect and no more**: a call to a function defined in the SAME FILE whose body
  can panic is treated as panicking, because that is what skipped the reap in `control_lifecycle.rs`
  -- `wait_for_socket` is a local function whose whole body is an `assert!`, and the only construct
  between the spawn and the reap was a call. Resolving that callee is not the interprocedural
  problem above: nothing changes hands. A helper in another crate is still an unknown contract.
* **Cross-FILE guard types are gathered, cross-CRATE ones are not.** A guard defined once in
  `tests/common/` is real protection and is read from the whole test scope (`crate_rust_context`),
  because `routines` does exactly this and a per-file reading reported its own fixed code as broken.
  A guard that lives in a *different crate* is a dependency's contract and is not read.
* **`unjudgeable`, reported and counted, never failed on.** A wrapper whose `Drop` cannot be located
  is named rather than passed, on the same reasoning as `is_locked`'s "an unanswerable question must
  not read as free" -- and deliberately not failed on, because a shape a text pass cannot judge
  failed on is a shape that teaches everyone to ignore the gate.
* **`#[cfg(test)] mod tests` inside `src/` is in scope**, found by content rather than by path,
  because a path-only scope cannot see a unit test at all.
* **Three languages.** Rust, Python and shell. Swift, JS/TS and Go are not read; the estate sweep
  measured how many spawn sites that leaves unchecked (see `docs/map.md`).
* **A shape a text pass cannot judge:** `Command::new` behind a helper that is itself a daemon
  spawner, and a `Child` moved into an `Arc`/`Mutex` for a later test to reap. Both are handoffs.
* **A `kill` inside a conditional is still accepted.** `if cond { child.kill(); }` satisfies the
  rule statically and does nothing when `cond` is false. Unchanged; needs a corpus, not a regex.

# SCOPE

TEST sources only. `tests/`, `benches/`, `__tests__/`, `spec/`, `test_*.rs`, `*_test.rs`,
`test_*.py`, `*_test.py`, `*.test.*`, `*.spec.*`, `*.bats`, plus any file carrying `#[cfg(test)]`.
A daemon started by the PROGRAM is the program's job; only a test that leaks one is a defect.

# EXIT CODES

0 clean (or: no test sources at all, NAMED) · 1 a finding · 2 usage/config error. A repo whose
test roots were renamed so the scan matched nothing is not a pass -- `check_empty_scope.py` sweeps
every gate for exactly that, and reads the phrase "not applicable" as a declared non-run rather
than a green light.

`--staged` reads THE INDEX via `checks/_gitutil.py`, never the worktree.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import content_bytes, listed_files, repo_root  # noqa: E402
from _spawn_mask import MASK  # noqa: E402
from _spawn_py_sh import python_findings, shell_findings  # noqa: E402
from _spawn_rust import rust_findings, rust_region  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, info, ok  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# ── scope ─────────────────────────────────────────────────────────────────────
TEST_DIRS = ("tests", "benches", "spec", "__tests__", "test", "testing", "e2e")
TEST_NAME = re.compile(r"(?:^|[._-])(?:test|tests|spec)[._-]|_(?:test|spec)s?\.[a-z]+$")
BATS = (".bats",)

# ── the scan ──────────────────────────────────────────────────────────────────


def is_test_file(rel: str, masked: str) -> bool:
    parts = rel.split("/")
    if any(p in TEST_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    if name.endswith(BATS) or TEST_NAME.search(name):
        return True
    # A Rust unit-test module inside src/ has no path tell at all; the marker does.
    return "#[cfg(test)]" in masked or "#[cfg(test)]" in masked.replace(" ", "")


# THE ONE ENTRY POINT. Masking is part of reading the file, so it cannot be something a caller has to
# remember: this gate's own probe handed RAW source to an analysis function that expects masked
# source, scored the masker's absence as a finding, and reported a disagreement the gate itself
# never had -- a self-proof disagreeing with the thing it proves. `scan`, `analyse` and `--probe`
# all arrive through here, so they cannot.
def _rust_verdicts(masked: str, crate: str = ""):
    return rust_findings(rust_region(masked), crate)


ANALYSE = {
    ".rs": _rust_verdicts,
    ".py": python_findings,
    ".sh": shell_findings,
    ".bash": shell_findings,
}


def verdicts(text: str, ext: str) -> list[tuple[int, str, str]]:
    """[(lineno, tag, why)] for one file's text, masked and analysed."""
    return ANALYSE[ext](MASK[ext](text))


REPORTED = ("handoff", "watchdog", "unjudgeable")

# THE CRATE-SCOPE SHARED GUARD. `routines` defines `ReapOnDrop` once in
# `tests/lifecycle_support/mod.rs` and three test files wrap their children in it; a per-file reading
# calls every one of them an unjudgeable wrapper. Measured over the estate, that is TWO files, and
# they are two of the three this release exists for -- so the cost of not gathering guard types at
# crate scope is a false alarm on the exact code the fix was written to protect.
#
# Bounded to test sources and to the crate, so a `vendor/` tree a `GOH_EXCLUDE` already dropped is
# not dragged back in through this side door: the same files `scan` examines, or fewer. Empty for a
# language with no guards, and it is a PREFIX so `verdicts` -- the one entry point the probe and the
# tests share -- keeps its one-argument shape.
_CRATE_RUST: list[str] = [""]


def crate_rust_context(root: str, paths, staged: bool, exclude) -> str:
    """Every test-source Rust file in the scope, masked and concatenated, for shared guards."""
    parts = []
    for rel in paths:
        if os.path.splitext(rel)[1] != ".rs" or (exclude and exclude.search(rel)):
            continue
        blob = content_bytes(root, rel, staged=staged)
        if blob is None or b"\0" in blob[:8000]:
            continue
        text = blob.decode("utf-8", "replace")
        if not is_test_file(rel, MASK[".rs"](text)):
            continue
        parts.append(MASK[".rs"](text))
    return "\n".join(parts)


def analyse(rel: str, text: str) -> tuple[list[str], dict[str, int]]:
    """(findings, counts) for one file. `REPORTED` tags are counted and shown, never failed on."""
    ext = os.path.splitext(rel)[1]
    masked = MASK[ext](text)
    hits = ANALYSE[ext](masked, _CRATE_RUST[0]) if ext == ".rs" else ANALYSE[ext](masked)
    findings = [f"{rel}:{n}: {tag}: {why}" for n, tag, why in hits if tag not in REPORTED]
    counts: dict[str, int] = {}
    for _, tag, _ in hits:
        if tag in REPORTED:
            counts[tag] = counts.get(tag, 0) + 1
    return findings, counts


def scan(root: str, paths, staged: bool, exclude) -> tuple[list[str], int, dict[str, int]]:
    bad: list[str] = []
    counts: dict[str, int] = {}
    examined = 0
    # Set BEFORE the first file is analysed, because a guard defined in one test file is protection
    # in another and the two are read in whatever order the file list happens to be.
    _CRATE_RUST[0] = crate_rust_context(root, paths, staged, exclude)
    for rel in paths:
        ext = os.path.splitext(rel)[1]
        if ext not in MASK or (exclude and exclude.search(rel)):
            continue
        blob = content_bytes(root, rel, staged=staged)
        if blob is None or b"\0" in blob[:8000]:
            continue
        text = blob.decode("utf-8", "replace")
        if not is_test_file(rel, MASK[ext](text)):
            continue
        examined += 1
        found, here = analyse(rel, text)
        bad.extend(found)
        for tag, n in here.items():
            counts[tag] = counts.get(tag, 0) + n
    return bad, examined, counts


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--staged", action="store_true", help="police the INDEX, not the worktree")
    ap.add_argument("--exclude", default="", help="regex on repo-relative paths (GOH_EXCLUDE)")
    ap.add_argument("--probe", action="store_true", help="prove this gate can go red")
    args = ap.parse_args(argv)
    if args.probe:
        # Beside this file so neither crowds the 500-line cap, and driven from here so the probe
        # cannot pass against a stale copy of the rules (the display-seam precedent).
        from _unreaped_spawn_probe import probe

        return probe()

    root = repo_root() or os.getcwd()
    try:
        listed = listed_files(root, staged=args.staged)
    except RuntimeError as exc:
        err(f"[no_unreaped_spawn] {exc}")
        info(
            "A git call that FAILS is not an empty tree. Fix the index, or the gate is not running."
        )
        return 2
    exclude = re.compile(args.exclude) if args.exclude else None
    bad, examined, counts = scan(root, listed, args.staged, exclude)

    if examined == 0:
        # Named, not "OK - 0 files". A gate that covered nothing prints exactly what a gate that
        # covered everything prints, and check_empty_scope.py reads this phrase as a non-run.
        ok("[no_unreaped_spawn] not applicable — this repo has no test source in scope")
        info(
            f"  looked for {', '.join(TEST_DIRS)}/, test_* / *_test names, *.bats, and #[cfg(test)]"
        )
        return 0

    if bad:
        err(f"[no_unreaped_spawn] {len(bad)} unreaped spawn(s) in test sources:")
        for hit in bad[:40]:
            err(f"    {hit}")
        info(
            "The only shape that survives a panic is a GUARD: wrap the Child in a ReapOnDrop whose"
        )
        info("Drop impl calls kill() and wait(), or use `with subprocess.Popen(...)`, or a shell")
        info("`trap cleanup EXIT` that kills $!. If the reap is explicit, put it ABOVE the first")
        info(
            "assertion -- the ordering IS the defect (media_server, 2026-10-03: nine live orphans,"
        )
        info("each holding the cargo build lock, and the only observable was a test run printing")
        info("nothing at all). A gate with no ceiling on its own wait is the other half: see")
        info("lib/bounded_run.py and GOH_LCI_TIMEOUT / GOH_STEP_TIMEOUT.")
        return 1
    tail = "; ".join(f"{n} {tag}" for tag, n in sorted(counts.items())) if counts else ""
    ok(
        f"[no_unreaped_spawn] OK — {examined} test file(s), every spawned child reaped on a panic"
        + (
            f" ({tail}; neither is a finding, and both are counted so a gate cannot go quiet on them)"
            if tail
            else ""
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
