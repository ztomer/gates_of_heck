"""The PYTHON and SHELL rules. Split out of `_spawn_shapes.py` at the 500-line cap.

    checks/_spawn_py_sh.py            # nothing to run; import it

The cut is at a real boundary: everything here takes ALREADY-MASKED text and knows nothing about
files, git, or what a test is, so these functions have no call site outside the three analysers.
The Rust rules are in `_spawn_rust.py`.

THE MEASURED TABLE behind these rules is in `check_no_unreaped_spawn.py`'s docstring and is
re-asserted row by row by `checks/_unreaped_spawn_probe.py`. Do not widen or narrow a rule here
without the probe going red: the table is the specification, and a docstring is not.
"""

import re

# ── Python ────────────────────────────────────────────────────────────────────
PY_POPEN = re.compile(r"(?<![\w.])(?:subprocess\s*\.\s*)?Popen\s*\(")
# `subprocess.run`/`check_output`/`call` wait internally, so they cannot leak and are not spawn
# sites here -- the same measured reasoning as Rust's `output()`.
PY_REAP = re.compile(r"\.\s*(?:wait|communicate|kill|terminate)\s*\(")
PY_WITH = re.compile(r"^\s*with\b|\bwith\b")
# A construct that can leave the function BEFORE a reap placed below it. `return` is here for the
# same reason it is in the Rust `PANICS`: it is an exit that skips the code under it, which is the
# whole shape of the defect. Its absence was found by reading the one site this gate cannot see --
# `games/ZeroThunder`'s `tests/e2e/garden_drag_flicker.py:73` spawns the app, then
# `if not z.wait_for_app(...): return 2` on the readiness path, and reaches `proc.terminate()` only
# on the path that worked. A `return` is not a panic; it is the ordinary way a function ends, and
# ending before the reap leaks the child exactly as an assertion failure does.
PY_PANIC = re.compile(
    r"^\s*(?:assert|raise\b|return\b|self\.fail\b|pytest\.fail\b|.*\.unwrap\s*\()|sys\.exit\s*\("
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

    BOTH SHAPES, and the second one was a blind spot the first version could not have found because
    nothing pointed at it. Forward: the `try:` comes AFTER the spawn -- it wraps the body. Wrapping:
    the `try:` is ABOVE the spawn and the `finally:` below it at a SHALLOWER indent, which is the
    ordinary `try:` around a whole function body. Searching forward only for an opener at the
    spawn's own indent reported `games/ZeroThunder`'s `tests/e2e/real_app_e2e.py:82` as a leak --
    a `try:` at line 74 with `finally: proc.terminate()` at line 118.
    """
    indent = len(lines[i]) - len(lines[i].lstrip())
    opener = None
    for j in range(i + 1, min(end, i + 80)):
        line = lines[j]
        if not line.strip():
            continue
        here = len(line) - len(line.lstrip())
        if here < indent:
            break
        if here == indent and line.rstrip().endswith(":"):
            opener = (j, line.strip())
            break
    if opener is not None and opener[1].startswith("try:"):
        fin = next(
            (
                k
                for k in range(opener[0] + 1, end)
                if lines[k].strip().startswith("finally:")
                and len(lines[k]) - len(lines[k].lstrip()) == indent
            ),
            None,
        )
        if fin is not None and _py_finally_body_reaps(lines, fin + 1, indent, end):
            return True
    # The wrapping form: the dedent below the spawn IS this try's `finally:`.
    for j in range(i + 1, min(end, i + 200)):
        line = lines[j]
        if not line.strip():
            continue
        here = len(line) - len(line.lstrip())
        if here < indent:
            return line.strip().startswith("finally:") and _py_finally_body_reaps(
                lines, j + 1, here, end
            )
    return False


def _py_finally_body_reaps(lines, start, indent, end):
    """Does the `finally:` block that begins at `start` reap?"""
    for k in range(start, end):
        body = lines[k]
        if not body.strip():
            continue
        if len(body) - len(body.lstrip()) <= indent:
            break
        if PY_REAP.search(body):
            return True
    return False


def _py_observed_exit(lines, j, binding):
    """Did the code just above this `return` establish that the child is ALREADY GONE?

    `if proc.poll() is not None: return None` is not a leak: `poll()` returning non-None means the
    process has exited, so there is nothing left to reap but a zombie. Two of the estate's
    offscreen harnesses (`control_honesty.py` and `garden_flora_band.py`, byte-for-byte the same
    boot loop) are written exactly that way, and the `return` rule below flagged both as leaks.
    """
    if not binding:
        return False
    look = "\n".join(lines[max(0, j - 3) : j])
    return bool(
        re.search(rf"\b{re.escape(binding)}\s*\.\s*(?:poll|returncode)\b|\bwaitpid\b", look)
    )


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
        early = [
            j
            for j in range(i + 1, stop)
            if PY_PANIC.search(lines[j]) and not _py_exits_but_hands_off(lines, j, stop)
        ]
        if early:
            what = "a raising line" if PY_PANIC.search(lines[early[0]]) else "an early return"
            out.append(
                (
                    i + 1,
                    "reap-after-panic",
                    f"the reap is at line {stop + 1}, AFTER {what} at {early[0] + 1}",
                )
            )
    return out


# The name the Popen is bound to, when it is bound to one.
PY_BIND = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:subprocess\s*\.\s*)?Popen\s*\(")
PY_RETURN = re.compile(r"^\s*return\b")


def _py_exits_but_hands_off(lines, j, stop):
    """Is the `return` at line `j` an exit that gives the handle onward, rather than skipping the reap?

    `return proc` on the success path of a boot helper is the FIX for the very defect this gate
    exists for -- `games/ZeroThunder`'s `tests/e2e/live_probe_lib.py` is the repaired version, and
    its `return proc` at line 42 sits above a `terminate`/`wait` that only runs on the failure path.
    Reading any `return` as "skips the reap" reported the repair as the defect, which is the one
    outcome a gate like this must never produce.
    """
    if not PY_RETURN.match(lines[j]):
        return False
    if _py_observed_exit(lines, j, _py_binding_at(lines, j, stop)):
        return True
    m = PY_BIND.search(lines[j])
    if m:
        return False
    earlier = [PY_BIND.search(lines[k]) for k in range(max(0, j - 60), j)]
    names = [x.group(1) for x in earlier if x]
    return any(re.search(rf"\b{re.escape(n)}\b", lines[j]) for n in names)


def _py_binding_at(lines, j, stop):
    for k in range(max(0, j - 60), j):
        m = PY_BIND.search(lines[k])
        if m:
            return m.group(1)
    return None


# ── shell ─────────────────────────────────────────────────────────────────────
# A background launch: a `&` that ends a command. `&&` is not one, `&>` is a redirect, `>&` is a file
# DESCRIPTOR dup or close -- `exec 3>&-` closes one and is not a launch at all, which was found by
# the estate sweep on `games/ZeroThunder`'s `tests/run_snow_visual_verify.sh:367`. A `&` inside
# quotes was already blanked by the masker.
BG = re.compile(r"(?<![\w)&|>])(?<!>)&(?![&\w>])")
SH_REAPS = re.compile(r"(?<![\w])wait\b|(?<![\w])trap\b|(?<![\w])kill\b")
# `name() {` / `function name {` / `function name() {` -- a helper that launches a server and cleans
# it up, which is most of what a shell test file is.
SH_FN = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(\s*\))?\s*\{\s*$")


def _sh_functions(lines):
    """[(start, end)] for each shell FUNCTION, found at brace depth zero only.

    Brace DEPTH, not "the nearest preceding `name() {`": the first version did the latter and
    treated every top-level statement after the first function in a file as being inside it, which
    reported all seven of the estate's shell launches as leaks. `${TMP:-x}` and `$(cmd)` are brace
    balanced, so counting `{` minus `}` across lines is sound.
    """
    starts, depth = [], 0
    for j, line in enumerate(lines):
        if depth == 0 and SH_FN.match(line):
            starts.append(j)
        depth += line.count("{") - line.count("}")
    out = []
    for s in starts:
        d = 0
        for k in range(s, len(lines)):
            d += lines[k].count("{") - lines[k].count("}")
            if d <= 0 and k >= s:
                out.append((s, k))
                break
        else:
            out.append((s, len(lines) - 1))
    return out


def shell_findings(masked: str) -> list[tuple[int, str, str]]:
    out = []
    lines = masked.split("\n")
    fns = _sh_functions(lines)
    inside = set()
    for lo, hi in fns:
        inside.update(range(lo, hi + 1))
    # A `trap` set OUTSIDE every function is shell-GLOBAL: it fires when the script exits however it
    # exits, from wherever. Dropping it because the launch sits inside a helper turned seven correct
    # scripts into seven findings on the first estate sweep, so it is honoured on its own terms.
    script_trap = any(
        j not in inside and re.search(r"(?<![\w])trap\b", lines[j]) for j in range(len(lines))
    )
    for i, line in enumerate(lines):
        code = line.rstrip()
        if not code or code.endswith("&&") or "&>" in code:
            continue
        if not BG.search(code):
            continue
        block = next(((lo, hi) for lo, hi in fns if lo <= i <= hi), (0, len(lines) - 1))
        if script_trap or SH_REAPS.search("\n".join(lines[block[0] : block[1] + 1])):
            continue
        out.append(
            (
                i + 1,
                "unreaped-spawn",
                "a backgrounded command with no kill, no wait and no trap: it outlives the test",
            )
        )
    return out
