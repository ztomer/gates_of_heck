#!/usr/bin/env python3
"""Run every gate against a repo with no CONTENT, and fail the ones that pass.

    check_empty_scope.py              # the sweep
    check_empty_scope.py --list       # what it would run
    check_empty_scope.py --probe      # prove this gate can go red

WHY. A gate whose subject population is empty prints the same word as a gate that scanned
everything and found nothing wrong. That is the single most common defect in this estate's gates,
it has been found in three repos, and it is invisible to review because the code looks correct --
the scan is right, the comparison is right, and the list it iterates is just empty.

What makes it dangerous is that the causes are ROUTINE. A file split moved CadGoose2's router and
`check_cli_coverage` reported "all 0 daemon verbs are reachable" for weeks. A renamed resources
directory would have retired two necrohand gates. Nobody edits a gate to break it; they edit the
tree it reads.

And a ratchet makes it worse, because a population that drops to zero reads as every ceiling being
met. necrohand's `check_palette_lineage` printed "0 of 0 arms drawn from their hand's palette" and
the shared ratchet beneath it NAMED all 25 vanished themes in the same output -- and still exited
0. The gate said its subject was gone and called that success.

HOW. A throwaway git repo holding the repo's real gate directory (so baselines, allowlists and
manifests come with it) and its whole directory SKELETON with no files. Every gate runs there. A
gate that exits 0 has reported compliance over nothing.

WHY A GREP WILL NOT DO, measured 2026-09-05 on necrohand: grepping for a guard flagged 17 of 22
gates and was wrong in both directions. It accused three that were fine -- one of them
`check_mcp_server`, which carries a test-count FLOOR written for exactly this class -- and missed
all three that were really blind. Running the gates is the only thing that answers the question.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import repo_root  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, info, ok, warn  # noqa: E402

GATE_DIRS = ("tools", "checks")
ALLOW_FILE = "empty_scope_allow.json"

# Root files that give the skeleton its SHAPE. Without them a gate may die on "this is not a Swift
# package" -- a legitimate refusal, but it answers a different question than the one being asked,
# and it would hide a blind gate behind a shape complaint.
SHAPE_FILES = (
    "Package.swift", "Makefile", "pyproject.toml", "Cargo.toml", "CMakeLists.txt",
    ".gatesrc", "package.json",
)

# Skipped by name, always: running this gate inside its own skeleton builds another skeleton.
SELF = "check_empty_scope.py"

TIMEOUT = 90

# Directories never worth copying into a skeleton -- large, generated, and irrelevant to the
# question. Their absence is part of the point.
SKIP_DIRS = {".git", ".build", "build", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache"}


def gate_dir(root):
    """The directory this repo keeps its gates in, or None."""
    for name in GATE_DIRS:
        path = os.path.join(root, name)
        if os.path.isdir(path) and any(
            e.startswith("check_") and e.endswith(".py") for e in os.listdir(path)
        ):
            return path
    return None


def allowed(gates):
    """({legitimate}, {known_blind}), each {gate: reason}.

    TWO sections, because collapsing them is how a burn-down list becomes permanent permission.
    `legitimate` is a gate that SHOULD pass on an empty tree and always will -- one that scans the
    gate directory itself, or compares two manifests that are still present. `known_blind` is a
    defect with a name on it, awaiting a guard; it may only ever shrink. A single undifferentiated
    allowlist would let the second quietly become the first, which is the exact rot the stale
    check below exists to catch one level down.
    """
    path = os.path.join(gates, ALLOW_FILE)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}, {}
    if "legitimate" not in data and "known_blind" not in data:
        # A flat file from before the split: treat every entry as a defect awaiting a guard,
        # which is the conservative reading.
        return {}, {k: v for k, v in data.items() if not k.startswith("_")}
    return (
        {k: v for k, v in data.get("legitimate", {}).items() if not k.startswith("_")},
        {k: v for k, v in data.get("known_blind", {}).items() if not k.startswith("_")},
    )


def build_skeleton(root, gates, workdir):
    """A git repo with the real gate directory, the shape files, and every directory -- no content.

    The gate directory is copied WHOLE and on purpose: its baselines and allowlists must come
    along, because "the baseline names 49 files and the scan found none" is precisely the state
    being tested for. A skeleton without them tests nothing.
    """
    subprocess.run(["git", "init", "-q", workdir], check=True, capture_output=True)
    shutil.copytree(gates, os.path.join(workdir, os.path.basename(gates)),
                    ignore=shutil.ignore_patterns(*SKIP_DIRS))
    for name in SHAPE_FILES:
        source = os.path.join(root, name)
        if os.path.isfile(source):
            shutil.copy2(source, os.path.join(workdir, name))
    for current, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in dirnames:
            rel = os.path.relpath(os.path.join(current, name), root)
            os.makedirs(os.path.join(workdir, rel), exist_ok=True)
    for key, value in (("user.email", "scope@example.invalid"), ("user.name", "scope")):
        subprocess.run(["git", "-C", workdir, "config", key, value], check=True,
                       capture_output=True)
    # The gate directory is on DISK (the gates must be runnable, and their baselines readable)
    # but deliberately NOT TRACKED. Otherwise every whole-repo scanner finds the copied gates,
    # does real work on them, and passes honestly -- which reports as blindness. Measured: leaving
    # it tracked made check_no_emoji say "18 tracked files clean" and check_pyflakes "99 file(s)
    # clean" on a tree with no content. Untracked, a git-ls-files scanner sees what it would
    # really see, and answers the question actually being asked.
    subprocess.run(["git", "-C", workdir, "add", "-A", "--", ":!%s" % os.path.basename(gates)],
                   capture_output=True)
    subprocess.run(["git", "-C", workdir, "commit", "-qm", "skeleton", "--allow-empty"],
                   capture_output=True)
    return workdir


def sweep(skeleton, gate_name, names, timeout=TIMEOUT):
    """{gate: first line of output} for every gate that EXITED 0 over the empty tree."""
    blind = {}
    for name in names:
        try:
            result = subprocess.run(
                [sys.executable, os.path.join(skeleton, gate_name, name)],
                cwd=skeleton, capture_output=True, text=True, timeout=timeout, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue  # could not run it at all: not a claim of compliance
        if result.returncode == 0:
            lines = [ln for ln in (result.stdout + result.stderr).splitlines() if ln.strip()]
            blind[name] = lines[-1].strip()[:100] if lines else "(no output)"
    return blind


def gate_names(gates):
    return sorted(
        e for e in os.listdir(gates)
        if e.startswith("check_") and e.endswith(".py") and e != SELF
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--root", default=None)
    args = parser.parse_args(argv)
    if args.probe:
        return probe()

    root = args.root or repo_root()
    gates = gate_dir(root)
    if gates is None:
        info("no check_*.py gates in this repo; nothing to sweep")
        return 0

    names = gate_names(gates)
    if not names:
        info("no gates to sweep")
        return 0
    if args.list:
        for name in names:
            info(name)
        return 0

    legitimate, known_blind = allowed(gates)
    excused = {**legitimate, **known_blind}
    with tempfile.TemporaryDirectory() as workdir:
        skeleton = build_skeleton(root, gates, os.path.join(workdir, "skeleton"))
        blind = sweep(skeleton, os.path.basename(gates), names)

    unexcused = {k: v for k, v in blind.items() if k not in excused}
    # The other direction, and it is the one an allowlist loses: a gate excused here that now
    # FAILS on an empty tree has been fixed, and its excuse is permission nobody needs any more.
    stale = sorted(k for k in excused if k not in blind and k in names)

    for name, line in sorted(unexcused.items()):
        err(f"{name} PASSED over an empty tree: {line!r}")
    if unexcused:
        info("Each of these reported compliance having inspected nothing. A renamed directory or")
        info("a changed file convention retires them in silence, and a ratchet reads a population")
        info("that dropped to zero as every ceiling being met.")
        info(f"Add a guard, or excuse it in {os.path.join(os.path.basename(gates), ALLOW_FILE)}")
        info("with the reason it may legitimately pass on nothing.")
    for name in stale:
        err(f"{name} is excused here but now FAILS on an empty tree -- delete its excuse")

    if unexcused or stale:
        return 1
    ok(f"empty scope: {len(names)} gate(s) swept over an empty tree, "
       f"{len(names) - len(blind)} refused to report compliance")
    if known_blind:
        warn(f"{len(known_blind)} gate(s) are KNOWN blind and still unguarded -- "
             f"this list may only shrink, see {os.path.basename(gates)}/{ALLOW_FILE}")
    return 0


def probe():
    """A blind gate, a guarded one, an excused one, and the stale excuse."""
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        root = os.path.join(td, "repo")
        tools = os.path.join(root, "tools")
        os.makedirs(os.path.join(root, "Sources", "Deep"))
        os.makedirs(tools)
        subprocess.run(["git", "init", "-q", root], check=True, capture_output=True)

        def gate(name, body):
            with open(os.path.join(tools, name), "w", encoding="utf-8") as handle:
                handle.write(body)

        # Counts files under Sources and reports success regardless -- the class, exactly.
        gate("check_blind.py",
             "import os, sys\n"
             "n = sum(len(f) for _, _, f in os.walk('Sources'))\n"
             "print(f'ok {n} files clean')\n"
             "sys.exit(0)\n")
        # Same scan, with the guard.
        gate("check_guarded.py",
             "import os, sys\n"
             "n = sum(len(f) for _, _, f in os.walk('Sources'))\n"
             "if not n:\n"
             "    print('x scanned nothing')\n"
             "    sys.exit(1)\n"
             "sys.exit(0)\n")

        names = gate_names(tools)
        with tempfile.TemporaryDirectory() as work:
            skeleton = build_skeleton(root, tools, os.path.join(work, "s"))
            blind = sweep(skeleton, "tools", names)

        cases = [
            ("both gates are discovered", ["check_blind.py", "check_guarded.py"], names),
            ("the unguarded gate is caught passing over nothing", True, "check_blind.py" in blind),
            ("the guarded gate is not", False, "check_guarded.py" in blind),
            ("the sweep goes red", 1, main(["--root", root])),
        ]
        for label, want, got in cases:
            if want != got:
                err(f"probe: {label} (wanted {want!r}, got {got!r})")
                bad += 1
            else:
                ok(f"probe: {label}")

        # Excused: the sweep must go quiet.
        with open(os.path.join(tools, ALLOW_FILE), "w", encoding="utf-8") as handle:
            json.dump({"known_blind":
                       {"check_blind.py": "a fixture, excused to prove the list works"}}, handle)
        if main(["--root", root]) != 0:
            err("probe: an EXCUSED blind gate still failed the sweep")
            bad += 1
        else:
            ok("probe: an excused gate is skipped")

        # Stale: excuse the GUARDED one, which does not pass on nothing.
        with open(os.path.join(tools, ALLOW_FILE), "w", encoding="utf-8") as handle:
            json.dump({"known_blind": {"check_blind.py": "still blind"},
                       "legitimate": {"check_guarded.py": "excused, but it was fixed"}}, handle)
        if main(["--root", root]) == 0:
            err("probe: a STALE excuse passed -- the allowlist is a rug, not a ratchet")
            bad += 1
        else:
            ok("probe: an excuse for a gate that now fails is reported")

        # A flat file predates the split; it must read as DEFECTS, never as legitimate.
        with open(os.path.join(tools, ALLOW_FILE), "w", encoding="utf-8") as handle:
            json.dump({"check_blind.py": "an old flat entry"}, handle)
        flat_legit, flat_blind = allowed(tools)
        if flat_legit or list(flat_blind) != ["check_blind.py"]:
            err("probe: a flat allowlist was not read conservatively as known-blind")
            bad += 1
        else:
            ok("probe: a pre-split flat allowlist reads as defects, not as legitimate")

    if bad:
        err(f"check_empty_scope --probe: {bad} case(s) wrong")
        return 1
    ok("check_empty_scope --probe: a blind gate is caught, a guarded one is not, both ways")
    return 0


if __name__ == "__main__":
    sys.exit(main())
