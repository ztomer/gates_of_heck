"""The RULES: what counts as a spawn, what reaps it, and what can panic in between.

    checks/_spawn_shapes.py            # nothing to run; import it

EXTRACTED from `check_no_unreaped_spawn.py` at the 500-line cap, and the cut is at a real
boundary: everything here takes ALREADY-MASKED text and knows nothing about files, git, or
what a test is, so none of these functions has a call site outside this module except the
three analysers.

THE MEASURED TABLE behind these rules is in `check_no_unreaped_spawn.py`'s docstring and is
re-asserted row by row by `checks/_unreaped_spawn_probe.py`. Do not widen or narrow a rule
here without the probe going red: the table is the specification, and a docstring is not.
"""

import re

# ── Rust ──────────────────────────────────────────────────────────────────────
# `.spawn()` with a DOT in front. `thread::spawn`, `tokio::spawn`, `rayon::spawn`,
# `spawn_blocking` and `wasm_bindgen` all use `::` or a bare name and are not process spawns.
SPAWN = re.compile(r"\.\s*spawn\s*\(")
CMD_NEW = re.compile(r"\b(?:std::process::)?Command\s*::\s*new\b|\bCommand\s*::\s*new\b")
FN = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)")
# A function that hands something back. `(?!...)` needs a NON-EMPTY body: written `(?!()|...)` the
# alternative is an empty group, it matches the empty string, the negative lookahead fails on every
# input, and `-> Child` reads as "returns nothing" -- so every handoff in the estate was a finding.
RETURNS = re.compile(r"->\s*(?!\(\s*\))")
# A panic-capable construct. `?` is included because an early return in a `Result`-returning test
# skips everything below it, exactly as a panic does.
PANICS = re.compile(
    r"(?<![\w:])((?:debug_)?assert(?:_eq|_ne)?!|panic!|unreachable!|todo!)\s*\(|\bprocess::abort\s*\("
    r"|\.\s*(?:unwrap|unwrap_or|unwrap_or_else|unwrap_err|expect)\s*\(|\bstd::process::exit\s*\("
    r"|\breturn\b|\?[\s,;)]*$"
)
# A crate that already ships the guard this gate asks people to write.
EXTERNAL_GUARDS = ("assert_cmd::Command", "escargot::Command")


def guard_types(masked: str) -> set[str]:
    """Local types whose `Drop` impl STOPS the child: `kill()` **or** `wait()`.

    Judged by what the impl DOES. A struct called `ReapOnDrop` that only logs would be accepted on
    its name, and the 2026-10-03 file shows why names are not evidence: the second defect in it was
    already "fixed" by a rename.

    `kill` AND `wait` was the first rule and it was WRONG, found by the estate sweep rather than by
    reading: `media_server/crates/mediaops-rs/tests/it/backup_snapshot.rs` has
    `impl Drop for Holder { drop(self.child.stdin.take()); let _ = self.child.wait(); }` -- closing
    stdin makes `sqlite3` exit and `wait` collects it -- and a kill-AND-wait rule called that a
    leak. `wait()` alone in a `Drop` IS the reap; requiring `kill` as well flags correct code.
    """
    found = set()
    for m in re.finditer(r"impl\s+Drop\s+for\s+(\w+)\s*\{", masked):
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


def _statement_end(lines, start, close, depth0):
    """(last line of the statement that begins at `start`, depth-relative `;` seen?).

    `depth0` is the brace depth AT the spawn, not at the line start: the spawn is usually the third
    line of a chained `let`, already several parens deep. Anchored at the line start it terminated
    early on a nested expression and handed the next lines to the panic scan as if they were part
    of the statement -- which is how a guard two lines below a `let` stopped counting as a guard.
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

    Brace depth, not indentation: rustfmt indents a nested `fn` inside a `mod`, and a text pass
    that trusted columns would read the wrong body.
    """
    depth = 0
    start = None
    for i in range(at, -1, -1):
        depth += lines[i].count("}") - lines[i].count("{")
        if depth <= 0:
            m = FN.match(lines[i])
            if m:
                start = i
                sig_from = i
                for j in range(at, -1, -1):
                    if FN.match(lines[j]):
                        sig_from = j
                        break
                depth = 0
                for j in range(sig_from, len(lines)):
                    depth += lines[j].count("{") - lines[j].count("}")
                    if depth <= 0 and j >= sig_from:
                        return sig_from, j
    return (start if start is not None else 0), len(lines) - 1


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
WATCHDOG = re.compile(r"thread::spawn|std::thread::spawn")
KILLER = re.compile(r"libc::kill\s*\(|\bkill\s*\(|SIGKILL")


def has_watchdog(body: str) -> bool:
    """A background thread in this body that kills a pid."""
    return bool(WATCHDOG.search(body)) and bool(KILLER.search(body))


def _ordering_finding(lines, end, reap):
    """The `reap-after-panic` finding, or None.

    A SEPARATE function with a name, because this rule is the one the whole gate exists for and a
    test has to be able to switch it off to prove the probe notices.
    """
    early = [j + 1 for j in range(end + 1, reap) if PANICS.search(lines[j])]
    if not early:
        return None
    return (
        f"the reap is at line {reap + 1}, AFTER a construct that can panic at line {early[0]} — a "
        "failing test skips the reap and leaks a server that never exits"
    )


def _is_command_spawn(lines, i):
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
    the shape every real call site has. The DOT in `SPAWN` is what excludes `thread::spawn`,
    `tokio::spawn` and `spawn_blocking`, which use `::` or a bare name.
    """
    floor = max(0, i - 8)
    for j in range(i, floor - 1, -1):
        if CMD_NEW.search(lines[j]):
            return True
        if j < i and re.search(r"[;{}]", lines[j]):
            return False
    return False


def rust_region(masked: str) -> str:
    """Blank everything OUTSIDE every `#[cfg(test)]` region, preserving line numbers.

    Measured on this repo's own sweep, 2026-10-03: `crates/goh/src/index_view.rs` carries a
    `#[cfg(test)] mod` at its BOTTOM and a `git checkout-index` spawn in production code at the
    top. A file-granular content scope reads the whole file as a test -- it reported that spawn,
    which is the gate's own production path, as a leak. The scope question is `#[cfg(test)]`-REGION
    granular, which is also how the compiler answers it.

    A `tests/` file has no such marker and is entirely in scope; when there is no marker at all this
    returns the text unchanged, so the ordinary case is untouched.
    """
    lines = masked.split("\n")
    marks = [n for n, line in enumerate(lines) if "cfg(test)" in line.replace(" ", "")]
    if not marks:
        return masked
    inside = [False] * len(lines)
    for at in marks:
        start = next((j for j in range(at, len(lines)) if "{" in lines[j]), len(lines) - 1)
        depth = 0
        for j in range(start, len(lines)):
            inside[j] = True
            depth += lines[j].count("{") - lines[j].count("}")
            if depth <= 0 and j >= start:
                break
    return "\n".join(line if keep else " " * len(line) for line, keep in zip(lines, inside))


def rust_findings(masked: str) -> list[tuple[int, str, str]]:
    """[(lineno, tag, why)] over one masked Rust file.

    The guard set is derived HERE rather than taken as an argument: it is the one input a caller
    gets wrong silently, and a probe that passed `set()` would report every guarded spawn as a leak
    -- which is the failure mode of a gate whose blind spot is its own interface.
    """
    guards = guard_types(masked)
    lines = masked.split("\n")
    out: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        if not SPAWN.search(line) or not _is_command_spawn(lines, i):
            continue
        sig, close = _fn_bounds(lines, i)
        end, semicolon = _statement_end(lines, i, close, _depth_at(lines, sig, i))
        statement = "\n".join(lines[i : end + 1])
        body = "\n".join(lines[sig : close + 1])
        # A guard ANYWHERE in the enclosing function: the spawn is wrapped in it, or the Child is
        # handed to a constructor named for it. BOTH are the shape that survives a panic, and
        # neither depends on where the binding sits.
        #
        # `Self { child }` is a construction too, and it is the form a factory uses -- measured on
        # `media_server/crates/mediaops-rs/tests/it/backup_snapshot.rs`, whose `Holder::open` ends
        # `Self { child }` and whose `Drop for Holder` closes stdin and waits. A check that only
        # recognised `GuardName(` called that correct guard a live orphan.
        built = rf"\b(?:{'|'.join(re.escape(g) for g in guards)}|Self)\s*[\{{(]" if guards else None
        if guards and (any(g in statement for g in guards) or re.search(built, body)):
            continue
        if any(g in statement for g in EXTERNAL_GUARDS):
            continue
        if any(re.search(rf"\b{re.escape(name)}\s*\(", body) for name in EXTERNAL_REAPERS):
            continue
        if has_watchdog(body):
            out.append(
                (
                    i + 1,
                    "watchdog",
                    "a background thread kills this child after a deadline: the leak is bounded, "
                    "not prevented — a ReapOnDrop guard is the shape that prevents it",
                )
            )
            continue
        if re.search(r"^\s*return\b", statement) or (not semicolon and _fn_returns(lines, sig)):
            out.append(
                (i + 1, "handoff", "spawn handed to the caller; this gate does not follow it")
            )
            continue
        reap = _reap_line(lines, end + 1, close)
        if reap is None:
            out.append(
                (
                    i + 1,
                    "unreaped-spawn",
                    "a Child that is never kill()ed or wait()ed: measured LIVE ORPHAN",
                )
            )
            continue
        early = _ordering_finding(lines, end, reap)
        if early:
            out.append((i + 1, "reap-after-panic", early))
    return out


def _reap_line(lines, start, close):
    """The line that STOPS the child (`kill`), or the `wait` when nothing kills it.

    Stop-or-collect, and the ORDER matters: `kill` ends the leak, `wait` only collects the zombie.
    A zombie cannot hold a build lock and cannot outlive the run, so `kill()` with no `wait()` is
    NOT this gate's business -- and flagging it would be flagging correct Rust, which is how a gate
    gets ignored. `wait()` alone DOES end the leak, because it blocks until the child exits.
    """
    stop = re.compile(r"\.\s*(?:kill|start_kill|terminate|send_signal)\s*\(")
    collect = re.compile(r"\.\s*(?:wait|wait_with_output|wait_timeout|try_wait)\s*\(")
    first_stop = first_collect = None
    for j in range(start, min(close + 1, len(lines))):
        if first_stop is None and stop.search(lines[j]):
            first_stop = j
        if first_collect is None and collect.search(lines[j]):
            first_collect = j
        if first_stop is not None and first_collect is not None:
            break
    if first_stop is not None:
        return first_stop
    return first_collect


# ── Python ────────────────────────────────────────────────────────────────────
PY_POPEN = re.compile(r"(?<![\w.])(?:subprocess\s*\.\s*)?Popen\s*\(")
# `subprocess.run`/`check_output`/`call` wait internally, so they cannot leak and are not spawn
# sites here -- the same measured reasoning as Rust's `output()`.
PY_REAP = re.compile(r"\.\s*(?:wait|communicate|kill|terminate)\s*\(")
PY_WITH = re.compile(r"^\s*with\b|\bwith\b")
PY_PANIC = re.compile(
    r"^\s*(?:assert|raise\b|self\.fail\b|pytest\.fail\b|.*\.unwrap\s*\()|sys\.exit\s*\("
)


def _py_block(lines, start):
    """The lines of the block `start` sits in: until a non-blank line at column 0.

    Indentation, not a regex: Python's block structure IS its indentation, and a `Popen` at module
    scope (a fixture constant, a `conftest` helper) must not scan the rest of the file for a `wait`
    that belongs to another test.
    """
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped and not lines[j][:1].isspace():
            end = j
            break
    return end


def _py_handed_off(lines, i, end):
    """The Popen leaves this function: `return ...Popen(` or stored on an object (`self.x = ...`).

    A HANDOFF, never a finding, and the rule is the same one Rust gets: once the child is not a
    local of this function, the reap is the CALLER's business and this checker does not follow it.
    Measured on this repo's own sweep: `tests/test_mcp_scaffold_wire.py`'s `DemoServer.__init__`
    stores the Popen on `self.proc` and a generator fixture's teardown terminates it -- correct
    code that a function-scoped reading calls a leak. Treating it as a finding would have taught
    everyone to ignore this gate, and it is the same shape the incident's own fix used.
    """
    statement = "\n".join(lines[i : min(i + 6, end)])
    head = lines[i]
    if re.match(r"^\s*return\b", head):
        return True
    # `self.proc = Popen(...)`, `cls.p = Popen(...)`, `holder.proc = Popen(...)`: a dotted target
    # is an attribute write, not a local binding.
    return bool(re.match(r"^\s*(?:self|cls)\.\w+\s*=", head)) or bool(
        re.match(r"^\s*\w+\.\w+\s*=\s*(?:subprocess\s*\.\s*)?Popen\s*\(", head)
    )


def _py_finally_reaps(lines, i, end):
    """Is the Popen wrapped in a `try: ... finally: <reap>`?

    `try`/`finally` is Python's kill-on-unwrap, and it is as common as Rust's `Drop` -- five of the
    seven hits this checker first reported across the estate were correct `try`/`finally` blocks
    (`tests/test_desktop_lock.py`, `tests/test_killtree.py`, three in `test_mcp_scaffold_wire.py`).
    A checker that cannot see a language's own unwind guard is a checker that flags the fix.

    The `try:` comes AFTER the spawn -- it wraps the body -- so the search runs FORWARD to the next
    line at this indent, and gives up on the first block opener that is not a `try`. Searching
    backwards, which is what the first version did, can never see it and reported every one of
    those five as a leak.
    """
    indent = len(lines[i]) - len(lines[i].lstrip())
    opener = None
    for j in range(i + 1, min(end, i + 80)):
        line = lines[j]
        if not line.strip():
            continue
        here = len(line) - len(line.lstrip())
        if here < indent:
            return False
        if here == indent and line.rstrip().endswith(":"):
            opener = (j, line.strip())
            break
    if opener is None or not opener[1].startswith("try:"):
        return False
    fin = next(
        (
            k
            for k in range(opener[0] + 1, end)
            if lines[k].strip().startswith("finally:")
            and len(lines[k]) - len(lines[k].lstrip()) == indent
        ),
        None,
    )
    if fin is None:
        return False
    for k in range(fin + 1, end):
        body = lines[k]
        if not body.strip():
            continue
        if len(body) - len(body.lstrip()) <= indent:
            break
        if PY_REAP.search(body):
            return True
    return False


def python_findings(masked: str) -> list[tuple[int, str, str]]:
    lines = masked.split("\n")
    out = []
    for i, line in enumerate(lines):
        if not PY_POPEN.search(line):
            continue
        if PY_WITH.search(line):
            continue  # `with subprocess.Popen(...) as p:` — __exit__ closes the pipes and waits
        end = _py_block(lines, i)
        if _py_handed_off(lines, i, end):
            out.append(
                (i + 1, "handoff", "the Popen leaves this function; the caller owes the reap")
            )
            continue
        if _py_finally_reaps(lines, i, end):
            continue
        stop = next((j for j in range(i + 1, end) if PY_REAP.search(lines[j])), None)
        if stop is None:
            out.append(
                (
                    i + 1,
                    "unreaped-spawn",
                    "Popen with no `with`, wait(), communicate() or kill(): measured LIVE ORPHAN",
                )
            )
            continue
        early = [j + 1 for j in range(i + 1, stop) if PY_PANIC.search(lines[j])]
        if early:
            out.append(
                (
                    i + 1,
                    "reap-after-panic",
                    f"the reap is at line {stop + 1}, AFTER a raising line at {early[0]}",
                )
            )
    return out


# ── shell ─────────────────────────────────────────────────────────────────────
# A background launch: a `&` that ends a command. `&&` is not one, `&>` is a redirect, and a `&`
# inside quotes was already blanked by the masker.
BG = re.compile(r"(?<![\w)&|])&(?![&\w>])")
SH_REAPS = re.compile(r"(?<![\w])wait\b|(?<![\w])trap\b|(?<![\w])kill\b")


def shell_findings(masked: str) -> list[tuple[int, str, str]]:
    out = []
    for i, line in enumerate(masked.split("\n")):
        code = line.rstrip()
        if not code or code.endswith("&&") or "&>" in code:
            continue
        if not BG.search(code):
            continue
        if SH_REAPS.search(masked):
            continue
        out.append(
            (
                i + 1,
                "unreaped-spawn",
                "a backgrounded command with no kill, no wait and no trap: it outlives the test",
            )
        )
    return out
