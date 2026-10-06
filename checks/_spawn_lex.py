"""The shared Rust VOCABULARY: what a spawn looks like, what a guard looks like, what can panic.

    checks/_spawn_lex.py            # nothing to run; import it

These are the regexes and constant lists that `_spawn_rust.py` (what counts as a spawn, which
function contains it, where its reap is) and `_spawn_guard_attr.py` (what protects it) both need to
agree on. They are in ONE module because that agreement is load-bearing and duplicated definitions
drift: a `SPAWN` that matches in one file and not the other is a gate that reports clean over half
the estate and a red over the other half, with no way to tell which is which.

This module exists because those two files import EACH OTHER -- `_spawn_rust` hands the attribution
questions down, `_spawn_guard_attr` asks them -- and a cycle between two modules that a reader opens
side by side is a `KILLER` pattern that got 10 lines away from being an import error. The vocabulary
is below both of them, so the dependency runs one way: `lex -> {rust, guard_attr}`.

Nothing here reads a file, knows what a test is, or needs masking. A pattern belongs here only if
BOTH files ask the same question about it.
"""

import re

# `.spawn()` with a DOT in front. `thread::spawn`, `tokio::spawn`, `rayon::spawn`,
# `spawn_blocking` and `wasm_bindgen` all use `::` or a bare name and are not process spawns.
SPAWN = re.compile(r"\.\s*spawn\s*\(")
CMD_NEW = re.compile(r"\b(?:std::process::)?Command\s*::\s*new\b|\bCommand\s*::\s*new\b")
FN = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)")
# A function that hands something back. `(?!...)` needs a NON-EMPTY body: written `(?!()|...)` the
# alternative is an empty group, it matches the empty string, the negative lookahead fails on every
# input, and `-> Child` reads as "returns nothing" -- so every handoff in the estate was a finding.
# The whitespace is INSIDE the look-ahead: written `->\s*(?!...)`, the engine gave `\s*` back
# and `-> ()` with a space matched, so a unit function read as handing its Child off.
RETURNS = re.compile(r"->(?!\s*\(\s*\))")
# A panic-capable construct. `?` is included because an early return in a `Result`-returning test
# skips everything below it, exactly as a panic does.
PANICS = re.compile(
    r"(?<![\w:])((?:debug_)?assert(?:_eq|_ne)?!|panic!|unreachable!|todo!)\s*\(|\bprocess::abort\s*\("
    r"|\.\s*(?:unwrap|unwrap_or|unwrap_or_else|unwrap_err|expect)\s*\(|\bstd::process::exit\s*\("
    r"|\breturn\b|\?[\s,;)]*$"
)
# The `let` that OWNS a spawn. `let mut owner = Command::new(…)` -- the guard question is about this
# variable and no other, and this is the pattern that answers it.
LET_BIND = re.compile(r"\blet\s+(?:mut\s+)?([A-Za-z_][A-Za-z0-9_]*)\b")
# A crate that already ships the guard this gate asks people to write.
EXTERNAL_GUARDS = ("assert_cmd::Command", "escargot::Command")
# A CALL on the child handle that reaps it by contract rather than by this file's own code. Found
# by the sweep on `sys_updater/crates/sys_updater_lib/src/runner_tests.rs`, which spawns `sh -c
# "sleep 30"` and hands the Child straight to `wait_timeout(&mut child, ..)` -- the helper under
# test, whose whole contract is stop-or-wait. Without this list the gate calls that a leak, and the
# fix is to stop using the crate's own helper, which is the opposite of right.
EXTERNAL_REAPERS = (
    "wait_timeout",
    "wait_for_output",
    "wait_for_exit",
    "wait_child",
    "kill_and_wait",
    "stop_and_wait",
    "terminate_and_wait",
    "reap_child",
    "spawn_guarded",
    "guard_child",
)
# A deadline WATCHDOG: a background thread that SIGKILLs this child's pid after N seconds. Found on
# `media_server/crates/lan-ip-watcher-rs/tests/test_cli.rs`, which spawns the watcher daemon,
# records `child.id()`, and arms `thread::spawn(move || { sleep(DEADLINE); libc::kill(pid,
# SIGKILL) })`. It is not a guard -- the child really does outlive the test -- but the leak is BOUNDED
# by the deadline, so it cannot outlive the suite, and a gate that failed it would be failing the
# second-best answer to this defect. Accepted, and COUNTED in the pass line, because a mitigation
# nobody can see is a mitigation that decays.
WATCHDOG = re.compile(r"thread::spawn|std::thread::spawn")
KILLER = re.compile(r"libc::kill\s*\(|\bkill\s*\(|SIGKILL")
# `impl Drop for X {` AND `impl Drop for X<'_> {` AND `impl<T> Drop for X<T> {`. The first version
# required `{` straight after the name, so a guard carrying a lifetime or a parameter -- ordinary in
# a `tests/` support module -- was not in the set at all, and a correctly guarded spawn was reported
# as a leak. `[^{]*` spans the generic argument, the lifetime and any `where` clause.
DROP_IMPL = re.compile(r"\bimpl(?:\s*<[^>]*>)?\s+Drop\s+for\s+([A-Za-z_][A-Za-z0-9_]*)[^{]*\{")
# `impl Foo {`, `impl<T> Foo<T> {`, `impl Drop for Foo {`, `impl SomeTrait for Foo<'_> {`: the TYPE
# an `impl` block's `Self` names. `Self` in a free function is not legal Rust, so every `Self` that
# matters resolves here.
IMPL_TYPE = re.compile(r"^\s*impl(?:\s*<[^>]*>)?\s+(?:.*\bfor\s+)?([A-Za-z_][A-Za-z0-9_]*)")
# The receiver of a `.spawn()` on a bare identifier: `cmd.spawn()`. Not end-anchored: the real shape
# is `Self(command.spawn().unwrap())`, where the spawn is mid-line and the `Child` is wrapped by a
# TUPLE FIELD on the way out. Anchored, `monitor`'s guard constructor was not seen as a spawn at all.
RECEIVER = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*spawn\s*\(")
# A DECLARATION of that identifier whose type is `std::process::Command`. `rayon::Scope::spawn` and
# `tokio::task::JoinHandle::spawn` both land on a bare identifier too, so the type is the whole
# question -- and it is answered from the declaration rather than from a name list.
CMD_DECL = re.compile(
    r"(?:\blet\s+(?:mut\s+)?{name}\s*[:=]|\b{name}\s*:\s*&?\s*mut\s+)"
    r"(?:\s*)(?:std::process::|process::)?Command\b"
)
