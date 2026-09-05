#!/usr/bin/env python3
"""Run every gate's self-proof, and fail if one of them has stopped working.

    check_probes_pass.py                 # run every probe this repo's gates declare
    check_probes_pass.py --list          # name them without running them
    check_probes_pass.py --probe         # prove THIS gate can go red

WHY THIS EXISTS, and it is a hole found by reading the ratchet that depends on it. The estate
counts a gate as PROVEN when its source matches a regex -- `def probe`, `--selftest`, and so on.
That is a claim about the presence of code, not about the behaviour of code. Nothing anywhere ran
those probes: not `gate.sh`, not `.gatesrc`, not CI. Twenty-four of them existed and were, on the
day this was written, all green -- but that was luck, not a gate, and luck is the state this whole
layer exists to replace.

The failure it prevents is specific and quiet. A probe drives its gate through a fixture; the gate
gets refactored -- a helper renamed, a scan taught to take a root, a check moved onto a shared
engine -- and the probe now raises on line one and nobody notices, because nothing runs it. The
gate keeps printing OK. The calibration ratchet keeps counting it as proven, because the regex
still matches. So the repo has a gate nobody has demonstrated can fail, a ratchet asserting the
opposite, and a green light over both. That is worse than having no probe at all: an unproven
gate is honestly unproven, while this one lies.

Process rule 3 is the reason it lives HERE rather than in each repo: gates are structural, not
disciplinary. A probe that has to be remembered is a probe that stops being run.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import repo_root  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, info, ok, warn  # noqa: E402

# Directories a repo keeps its gates in. Both, because gates_of_heck uses checks/ and every
# consuming repo uses tools/.
GATE_DIRS = ("tools", "checks")

# The flags a self-proof is invoked by, most common first. A gate declares one of these; this
# runner uses whichever it finds rather than assuming, because the estate genuinely has three
# spellings and renaming them all would be churn for no gain.
FLAGS = ("--probe", "--break-probe", "--selftest", "--self-test")

# A per-probe ceiling. A probe is a fixture-driven unit check; one that needs longer than this has
# quietly become an integration test and belongs somewhere it can be seen to be slow.
TIMEOUT = 180


def probe_flag(source):
    """The flag this gate's source says it accepts, or None if it declares no self-proof."""
    for flag in FLAGS:
        # Anchored to an argv test so a flag merely NAMED in prose does not count. The docstrings
        # in this estate discuss `--probe` constantly; only a gate that dispatches on it has one.
        if re.search(rf"{re.escape(flag)}\"?'?\s*(in sys\.argv|,|\))", source):
            return flag
    return None


def discover(root, dirs=GATE_DIRS):
    """[(path, flag)] for every gate under `root` that declares a self-proof, plus the total seen.

    Returns the total too, because "no probes" and "no gates" are different facts and the caller
    must be able to tell them apart -- the first is a repo with work to do, the second is a
    discovery convention that has moved out from under this check.
    """
    found, total = [], 0
    for name in dirs:
        directory = os.path.join(root, name)
        if not os.path.isdir(directory):
            continue
        for entry in sorted(os.listdir(directory)):
            if not (entry.startswith("check_") and entry.endswith(".py")):
                continue
            path = os.path.join(directory, entry)
            total += 1
            with open(path, encoding="utf-8", errors="replace") as handle:
                flag = probe_flag(handle.read())
            if flag:
                found.append((path, flag))
    return found, total


def run_one(path, flag, cwd, timeout=TIMEOUT):
    """(ok, detail). A probe must exit 0; anything else is the probe telling us it is broken."""
    try:
        result = subprocess.run(
            [sys.executable, path, flag],
            cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    if result.returncode == 0:
        return True, ""
    tail = (result.stdout + result.stderr).strip().splitlines()
    # The HEAD of a traceback names the probe that broke; the tail names the exception. Both are
    # wanted, and a probe that prints a hundred case lines must not bury either.
    detail = tail[:4] + (["    ..."] + tail[-4:] if len(tail) > 8 else tail[4:])
    return False, f"exit {result.returncode}\n" + "\n".join(f"    {line}" for line in detail)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="name the probes, run nothing")
    parser.add_argument("--probe", action="store_true", help="prove this gate can go red")
    parser.add_argument("--root", default=None, help="repo to scan (default: the enclosing repo)")
    args = parser.parse_args(argv)
    if args.probe:
        return probe()

    root = args.root or repo_root()
    probes, total = discover(root)

    if total == 0:
        # Not a failure: plenty of repos carry no gates of their own. Saying so is the point --
        # a silent pass here is indistinguishable from a pass over a directory that moved.
        info("no check_*.py gates in this repo; nothing to prove")
        return 0
    if not probes:
        warn(f"{total} gate(s) here and not one declares an INLINE self-proof -- any proof they\n"
         f"      have lives elsewhere, and this gate does not check that it still runs")
        return 0

    if args.list:
        for path, flag in probes:
            info(f"{os.path.relpath(path, root)} {flag}")
        return 0

    failed = []
    for path, flag in probes:
        passed, detail = run_one(path, flag, cwd=root)
        if not passed:
            failed.append((path, detail))

    for path, detail in failed:
        err(f"{os.path.relpath(path, root)} --probe is BROKEN: {detail}")
    if failed:
        err(f"{len(failed)} of {len(probes)} self-proofs failed.")
        info("A broken probe is worse than a missing one: the calibration ratchet still counts")
        info("the gate as proven, so a gate nobody has shown can fail sits under a green light.")
        info("Fix the probe against the gate as it is now, or delete it and lose the claim.")
        return 1

    ok(f"{len(probes)} of {total} gate(s) carry an inline self-proof, every one still passing")
    return 0


def probe():
    """Three cases: a passing proof, a BROKEN proof, and a gate that declares none."""
    import tempfile
    import textwrap

    bad = 0
    with tempfile.TemporaryDirectory() as td:
        tools = os.path.join(td, "tools")
        os.makedirs(tools)

        def gate(name, body):
            with open(os.path.join(tools, name), "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(body).lstrip())

        gate("check_good.py", """
            import sys
            if "--probe" in sys.argv:
                print("ok")
                sys.exit(0)
            sys.exit(0)
        """)
        gate("check_broken.py", """
            import sys
            if "--probe" in sys.argv:
                raise SystemExit("the fixture this probe drove was renamed")
            sys.exit(0)
        """)
        # Declares no self-proof, and MENTIONS --probe in prose -- the false positive the
        # anchored detector exists to refuse.
        gate("check_bare.py", '''
            """This gate has no --probe yet; see the backlog."""
            import sys
            sys.exit(0)
        ''')

        probes, total = discover(td)
        names = {os.path.basename(p) for p, _ in probes}

        for label, want, got in (
            ("all three gates are counted", 3, total),
            ("the two with a real self-proof are found", {"check_good.py", "check_broken.py"}, names),
            ("a gate that only MENTIONS --probe is not counted", False, "check_bare.py" in names),
            ("a passing proof passes", (True, ""),
             run_one(os.path.join(tools, "check_good.py"), "--probe", td)),
            ("a BROKEN proof is caught", False,
             run_one(os.path.join(tools, "check_broken.py"), "--probe", td)[0]),
            ("the whole run goes red on it", 1, main(["--root", td])),
        ):
            if want != got:
                err(f"probe: {label} (wanted {want!r}, got {got!r})")
                bad += 1
            else:
                ok(f"probe: {label}")

        os.remove(os.path.join(tools, "check_broken.py"))
        if main(["--root", td]) != 0:
            err("probe: a tree whose proofs all pass should be green")
            bad += 1
        else:
            ok("probe: a tree whose proofs all pass is green")

        empty = os.path.join(td, "empty")
        os.makedirs(empty)
        if main(["--root", empty]) != 0:
            err("probe: a repo with no gates at all should not fail")
            bad += 1
        else:
            ok("probe: a repo with no gates at all is reported, not failed")

    if bad:
        err(f"check_probes_pass --probe: {bad} case(s) wrong")
        return 1
    ok("check_probes_pass --probe: a broken self-proof goes red, and prose does not count as one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
