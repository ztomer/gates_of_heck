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

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _empty_scope_usage import first_error_line, is_usage_error  # noqa: E402
from _gitutil import foreign_repo_env, repo_root  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, info, ok, warn  # noqa: E402

GATE_DIRS = ("tools", "checks")
ALLOW_FILE = "empty_scope_allow.json"

# Root files that give the skeleton its SHAPE. Without them a gate may die on "this is not a Swift
# package" -- a legitimate refusal, but it answers a different question than the one being asked,
# and it would hide a blind gate behind a shape complaint.
SHAPE_FILES = (
    "Package.swift",
    "Makefile",
    "pyproject.toml",
    "Cargo.toml",
    "CMakeLists.txt",
    ".gatesrc",
    "package.json",
)

# Skipped by name, always: running this gate inside its own skeleton builds another skeleton.
SELF = "check_empty_scope.py"

TIMEOUT = 90

# Directories never worth copying into a skeleton -- large, generated, and irrelevant to the
# question. Their absence is part of the point.
SKIP_DIRS = {
    ".git",
    ".build",
    "build",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
}


def scratch_git(*args, check=False):
    """git on the skeleton or a probe fixture -- never the repo being gated. The hook's GIT_DIR
    and GIT_INDEX_FILE are dropped, or `git init <skeleton>` from a linked worktree's hook
    re-initialises the REAL repository (and flips its core.bare), and the skeleton's config,
    add and commit land there too. See `_gitutil.foreign_repo_env`."""
    return subprocess.run(["git", *args], check=check, capture_output=True, env=foreign_repo_env())


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
    scratch_git("init", "-q", workdir, check=True)
    shutil.copytree(
        gates,
        os.path.join(workdir, os.path.basename(gates)),
        ignore=shutil.ignore_patterns(*SKIP_DIRS),
    )
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
        scratch_git("-C", workdir, "config", key, value, check=True)
    # The gate directory is on DISK (the gates must be runnable, and their baselines readable)
    # but deliberately NOT TRACKED and IGNORED. Otherwise every whole-repo scanner finds the
    # copied gates, does real work on them, and passes honestly -- which reports as blindness.
    # Measured: leaving it tracked made check_no_emoji say "18 tracked files clean" and
    # check_pyflakes "99 file(s) clean" on a tree with no content. Untracked alone stopped being
    # enough on 2026-09-14, when full-scope listing became the WORKTREE (tracked + untracked,
    # minus ignored) so a brand-new file is policed before it is staged; the skeleton now also
    # ignores the gate dir via .git/info/exclude -- repo-local, never a tracked file, so it adds
    # no content of its own. A scanner then sees what it would really see over an empty tree.
    info_dir = os.path.join(workdir, ".git", "info")
    os.makedirs(info_dir, exist_ok=True)
    with open(os.path.join(info_dir, "exclude"), "a", encoding="utf-8") as fh:
        fh.write("/%s/\n" % os.path.basename(gates))
    scratch_git("-C", workdir, "add", "-A", "--", ":!%s" % os.path.basename(gates))
    scratch_git("-C", workdir, "commit", "-qm", "skeleton", "--allow-empty")
    return workdir


# A gate that exits 0 having SAID it did not run is not claiming compliance.
# The house convention for an absent tool or a foreign host is a named skip;
# this is the phrase the sweep recognises, so a macOS-only gate on a Linux
# runner (monitor's codesign check, 2026-09-14) reads as "did not apply"
# rather than "passed over nothing" -- without an excuse whose truth would
# depend on which host ran the sweep.
# The one non-run an excuse may cover: a gate that demands an argument is unrunnable BY DESIGN and
# the reason is recorded. A timeout is never excusable — being too slow is a property of the
# machine, not of the gate — which is why this is checked here rather than merely "is it excused".
BY_DESIGN = "demands an argument"

NOT_APPLICABLE = "not applicable"


UNMEASURABLE = "not measurable on an empty tree"


def _is_gate_runtime(module):
    """True when `module` is part of THIS checkout's runtime (tui, lib/, checks/): failing to import
    it is the sweep's own defect, never a property of the consumer's tree."""
    home = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return any(
        os.path.exists(os.path.join(home, where, module + suffix))
        for where in ("", "lib", "checks")
        for suffix in ("", ".py")
    )


def _runtime_env():
    """The checkers' RUNTIME, which copying them into the skeleton left behind: the skeleton mirrors
    `tui/` and `lib/` as empty directories, so a copied checker finds neither unless the sweep says
    where they really are. Content stays absent; only the libraries come along."""
    home = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = foreign_repo_env()
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (home, os.path.join(home, "lib"), env.get("PYTHONPATH", "")) if p
    )
    # ...and the dispatcher the native checks run through (`gates/goh.sh`), which a checker that
    # runs one (`check_estate_corpus.py`) finds through GOH_DIR, not beside its copied self.
    env["GOH_DIR"] = home
    return env


def sweep(skeleton, gate_name, names, timeout=None, excused=()):
    """(blind, unrunnable) over the empty tree.

    blind is {gate: last line of output} for every gate that EXITED 0 WITHOUT declaring itself not
    applicable. unrunnable is {gate: why} for every gate that could not be run at all.

    The second return value exists because "could not run" and "ran and refused" used to look
    identical downstream. A gate that TIMED OUT was skipped, and skipping is indistinguishable from
    passing, so a gate that never reported anything fell out of `blind` and was then read as
    "excused here but now FAILS on an empty tree -- delete its excuse". That verdict is a claim
    about what the gate said, and a gate that said nothing cannot support it: the excuse was
    correct all along and deleting it would have silenced a real guard over a slow machine.

    `timeout` defaults to the module's TIMEOUT at CALL time, not at def time, so the probe can
    shrink it and exercise this path in a second instead of a minute and a half.
    """
    timeout = TIMEOUT if timeout is None else timeout
    blind = {}
    unrunnable = {}
    # Each gate's temp files land beside the skeleton and go with it: a gate that runs a toolchain
    # (SwiftPM's TemporaryDirectory.*, a probe's log) left five entries in the shared $TMPDIR per
    # sweep, every sweep (2026-10-06, tests/test_gate_temp_leaks.py).
    scratch = os.path.join(os.path.dirname(os.path.abspath(skeleton)), "tmp")
    os.makedirs(scratch, exist_ok=True)
    env = {**_runtime_env(), "TMPDIR": scratch + os.sep}
    for name in names:
        try:
            result = subprocess.run(
                [sys.executable, os.path.join(skeleton, gate_name, name)],
                cwd=skeleton,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                # Inside the skeleton, not the hook's repo: with the hook's GIT_DIR a gate's git
                # calls would read the REAL tree, and the sweep would measure the wrong thing.
                env=env,
            )
        except subprocess.TimeoutExpired:
            unrunnable[name] = f"no verdict within {timeout}s"
            continue
        except (OSError, subprocess.SubprocessError) as exc:
            unrunnable[name] = f"could not run: {exc}"
            continue
        if "Traceback (most recent call last)" in result.stderr and re.search(
            r"^(ModuleNotFoundError|ImportError):", result.stderr, re.M
        ):
            # The SWEEP could not run it: an import its copy cannot resolve is the skeleton's defect,
            # and with no PYTHONPATH every checker died that way, each exit 1 scored as a refusal.
            # A checker that dies on the empty tree ITSELF (a missing subject file) did refuse --
            # loudly -- and stays a refusal (2026-10-05, tests/test_gate_runtime_path.py).
            last = result.stderr.strip().splitlines()[-1]
            missing = re.search(r"No module named '([^'.]+)", last)
            if missing and not _is_gate_runtime(missing.group(1)):
                # The consumer's OWN package: the skeleton has none of its content, by design, so
                # this gate cannot be measured on an empty tree. Named, never failing -- it is the
                # sweep's limit, not the gate's defect (divoom-control, ZeroThunder, gaf).
                unrunnable[name] = f"{UNMEASURABLE}: it imports its repo's own `{missing.group(1)}`"
            else:
                unrunnable[name] = f"crashed: {last[:100]}"
            continue
        if result.returncode == 0:
            text = result.stdout + result.stderr
            if NOT_APPLICABLE in text:
                continue  # a named non-run, not a pass
            lines = [ln for ln in text.splitlines() if ln.strip()]
            blind[name] = lines[-1].strip()[:100] if lines else "(no output)"
        elif is_usage_error(result.stdout + result.stderr):
            # A gate that DEMANDS an argument did not refuse to report compliance — it never got
            # to look. Scored as a pass it is worse than a blind gate, because it is silent AND
            # looks deliberate: `check_display_seam.py` exits 2 with argparse's usage line when
            # swept bare, and that landed in the healthy column. The timeout error one layer down.
            unrunnable[name] = f"{BY_DESIGN}, so it never looked: {first_error_line(result.stderr)}"
        # else: it ran and returned non-zero — a refusal, which is the healthy case.
    return blind, unrunnable


# A line that IS the forward, not a mention of it (this file names it, and is a gate).
FORWARDER = re.compile(r'^__import__\("_retired"\)\.forward\(', re.M)


def forwarders(gates):
    """The retired checkers' entry points (Phase N3): three lines that exec `goh.sh <check>`. Not
    gates of this repo -- the native check's empty-tree refusal is pinned by its own suite -- so
    they are named and skipped, never swept as a Python gate that cannot find its dispatcher."""
    out = []
    for e in sorted(os.listdir(gates)):
        if e.startswith("check_") and e.endswith(".py"):
            with open(os.path.join(gates, e), encoding="utf-8", errors="replace") as fh:
                if FORWARDER.search(fh.read()):
                    out.append(e)
    return out


def gate_names(gates):
    skip = set(forwarders(gates))
    return sorted(
        e
        for e in os.listdir(gates)
        if e.startswith("check_") and e.endswith(".py") and e != SELF and e not in skip
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--root", default=None)
    args = parser.parse_args(argv)
    if args.probe:
        from _empty_scope_probe import probe

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

    for name in forwarders(gates):
        info(f"{name} forwards to a native check (Phase N3) -- not a gate here, not swept")
    legitimate, known_blind = allowed(gates)
    excused = {**legitimate, **known_blind}
    # An excuse naming no gate is the same stale permission as one whose gate was fixed: it would
    # silently excuse the next file to take the name.
    orphaned = sorted(k for k in excused if k not in names)
    with tempfile.TemporaryDirectory() as workdir:
        skeleton = build_skeleton(root, gates, os.path.join(workdir, "skeleton"))
        blind, unrunnable = sweep(skeleton, os.path.basename(gates), names)

    unexcused = {k: v for k, v in blind.items() if k not in excused}
    # A gate excused because it demands an argument is DECLARED not run. It is still absent from
    # `blind`, so the stale-excuse ratchet would otherwise demand its excuse be deleted every run.
    declared = {k: v for k, v in unrunnable.items() if k in excused and v.startswith(BY_DESIGN)}
    unrunnable = {k: v for k, v in unrunnable.items() if k not in declared}
    unmeasurable = {k: v for k, v in unrunnable.items() if v.startswith(UNMEASURABLE)}
    unrunnable = {k: v for k, v in unrunnable.items() if k not in unmeasurable}
    # The other direction, and it is the one an allowlist loses: a gate excused here that now
    # FAILS on an empty tree has been fixed, and its excuse is permission nobody needs any more.
    # A gate that never reported is not such a gate, so it cannot retire an excuse.
    stale = sorted(
        k
        for k in excused
        if k not in blind
        and k in names
        and k not in unrunnable
        and k not in declared
        and k not in unmeasurable
    )
    for name, why in sorted(unmeasurable.items()):
        info(f"{name}: {why} -- its empty-tree behaviour is unmeasured here, not passed")

    for name, why in sorted(declared.items()):
        info(f"{name} is EXCUSED from the sweep: {why.splitlines()[0][:100] if why else ''}")
    for name, why in sorted(unrunnable.items()):
        warn(f"{name} gave NO verdict ({why}) -- its scope is unchecked, not clean")
    if unrunnable:
        info("A gate that could not run is not a gate that passed. Raise TIMEOUT if the machine is")
        info("merely loaded, or fix the gate; either way do not read its silence as compliance.")
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
    for name in orphaned:
        err(f"{name} is excused here but names no gate in this repo -- delete its excuse")

    if unexcused or stale or unrunnable or orphaned:
        return 1
    ok(
        f"empty scope: {len(names)} gate(s) swept over an empty tree, "
        f"{len(names) - len(blind)} refused to report compliance"
    )
    if known_blind:
        warn(
            f"{len(known_blind)} gate(s) are KNOWN blind and still unguarded -- "
            f"this list may only shrink, see {os.path.basename(gates)}/{ALLOW_FILE}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
