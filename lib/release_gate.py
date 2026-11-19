"""The release land's gate: a diff of release files only re-runs the checks that read them.

GOH.SHIPRELREUSE R3 (coordinator ruling 2026-11-15, owner-delegated: a ship at 10 min or less).
tools/release_goh.sh lands a commit that differs from the land-gated tip it was cut from only in
CHANGELOG.md and the changelog/ fragments it folds (R1: the version is the tag). Its land ran the
whole push gate again over bytes that gate had just judged. Now the kit hands tools/land.sh this as
its gate, with the tip it was cut from:

    release_gate.py run --base TIP --full CMD   # the land's gate (GOH_LAND_GATE), in the worktree
    release_gate.py decide --base TIP           # prints `release` or `full`
    release_gate.py names --base TIP            # the tests that read what TIP..HEAD changed
    release_gate.py push-decide [--root DIR]    # the push gate's rule for HEAD (below)

`run` diffs TIP against HEAD. Every path CHANGELOG.md or under changelog/ (and at least one): it
runs the structural gate (`gates/structural.sh --full`: every check over every tracked file, the
changelog's included) and then the tests `names` finds. Anything else -- one source file, an empty
diff, a TIP that is no ancestor because main moved and land.sh rebased -- runs CMD, the full gate,
unchanged. The named set is the tests that READ a changed path by the one rule the land's pre-step
and its verdict carry use (tools/affected_tests.py `readers`: a test naming `REPO_ROOT /
"CHANGELOG.md"`, importing or reaching a reader of it), so a new reader joins it by being written.
It was a text probe (any test whose text said CHANGELOG.md or changelog/): 26 files and 384 tests,
133 s at load 50, of which one file reads goh's own changelog -- the other 25 write a fixture's,
and their verdict on the tip holds (GOH.RELEASELANDGATE). Each part is a timed row
(lib/step_timings.py), so the land's profile names where its seconds went. Pinned by
tests/test_release_gate.py.

THE PUSH GETS THE SAME RULE (coordinator ruling 2026-11-15). The land above only moved the cost:
`git push origin main` ran the whole push gate over the release commit again. A repo declaring
GOH_PUSH_RELEASE_REUSE=on has gates/push_gate.sh ask `push-decide` for the pushed commit, and run
`run --base PARENT` (the set above) only when ALL hold: one parent; that parent's land was green --
tools/land.sh appends `SHA IDENTITY` to `goh-landed` in the common git dir only after a green gate
and the merge, so a red land and a commit no land judged are both absent, and IDENTITY must be
lib/gate_identity.py's for the full gate now (a hand-set GOH_LAND_GATE, other gates or tools
miss); and the diff parent..HEAD is release files only, by release_only() above, the one
definition the land uses. It prints `release PARENT` or `full REASON`, and push_gate.sh says
the reason. Pinned by tests/test_push_release_reuse.py, each calibration by the reason it names.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)

import argparse
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_identity  # lib/, beside this file: the one identity (GOH.VERDICTKEY)
import reuse  # lib/, beside this file: the one verdict store (GOH.ONEVERDICT)
import reuse_audit  # ... and its auditor (GOH.REUSEAUDIT)
import step_timings  # ... a timed row per part of the gate (GOH.RELEASELANDGATE)

GITHUB = re.compile(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$")

RELEASE_FILE = "CHANGELOG.md"
RELEASE_DIR = "changelog/"
READS_RELEASE_FILES = re.compile(r"CHANGELOG\.md|changelog/|[\"']changelog[\"']")
STRUCTURAL = "./gates/structural.sh --full"


def runner() -> str:
    """The probe's pytest: THIS gate's share of the cores (lib/worker_share.py, GOH.POOLSHARE),
    never xdist's automatic count, which is the machine's."""
    import worker_share  # lib/, beside this file

    python = shlex.quote(sys.executable)
    return f"{python} -m pytest -q -p no:cacheprovider -n {worker_share.share()} --dist loadgroup"


def names(root: Path, paths: list[str]) -> list[str]:
    """The tests that read a path of PATHS, relative to root: tools/affected_tests.py's readers."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools import affected_tests  # the kit's: the one rule of "a test reads this path"

    return sorted(affected_tests.readers(root, paths))


def release_only(paths: list[str]) -> bool:
    """True for a non-empty diff of CHANGELOG.md and changelog/ fragments and nothing else."""
    return bool(paths) and all(p == RELEASE_FILE or p.startswith(RELEASE_DIR) for p in paths)


def changed(root: Path, base: str, head: str = "HEAD") -> list[str] | None:
    """The paths head changed since base, or None when base is no ancestor of head (main moved)."""
    git = ["git", "-C", str(root)]
    if subprocess.run([*git, "merge-base", "--is-ancestor", base, head], check=False,
                      stdin=subprocess.DEVNULL, capture_output=True).returncode != 0:  # fmt: skip
        return None
    diff = [*git, "diff", "--no-renames", "--name-only", base, head]
    out = subprocess.run(diff, check=True, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return [line for line in out.stdout.splitlines() if line]


def decide(root: Path, base: str) -> str:
    paths = changed(root, base)
    return "release" if paths is not None and release_only(paths) else "full"


def _git_out(root: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(root), *args], check=True, stdin=subprocess.DEVNULL,
                         capture_output=True, text=True)  # fmt: skip
    return out.stdout.strip()


def land_identity(root: Path, sha: str) -> str | None:
    """The gate identity tools/land.sh recorded with sha, read through the one reuse API
    (lib/reuse.py landed_identity, GOH.ONEVERDICT): `""` for a line without one, None when no land
    recorded sha."""
    return reuse.landed_identity(root, sha)


def decide_push(root: Path, head: str = "HEAD") -> tuple[str, str]:
    """("release", PARENT) when head is release files only on a parent whose land was green;
    ("full", REASON) otherwise -- the reason is what the push says."""
    sha, *parents = _git_out(root, "rev-list", "--parents", "-n1", head).split()
    if len(parents) != 1:
        return "full", f"{sha[:8]} has {len(parents)} parents, not one"
    parent = parents[0]
    recorded = land_identity(root, parent)
    if recorded is None:
        return "full", f"its parent {parent[:8]} has no green land verdict (tools/land.sh's record)"
    if recorded != gate_identity.key("full"):
        why = "is not the full gate's under these gates and tools (lib/gate_identity.py)"
        return "full", f"its parent {parent[:8]}'s land verdict {why}"
    paths = changed(root, parent, sha) or []
    if not paths:
        return "full", f"it changes nothing since its parent {parent[:8]}"
    other = next((p for p in paths if not release_only([p])), None)
    if other is not None:
        return "full", f"{other} is not a release file (the diff since {parent[:8]})"
    return audited(root, parent, recorded, sha)


def audited(root: Path, parent: str, recorded: str, sha: str) -> tuple[str, str]:
    """The land verdict's reuse, through the audit (lib/reuse_audit.py path `landed`): a path the
    audit turned off is the full gate; every reuse queues its shadow, hosted CI's full gate of the
    pushed commit (goh's own lands and releases: CI runs it on every push), read by the lander."""
    fields = {"repo": reuse.repo_id(root), "sha": parent, "identity": recorded}
    if reuse_audit.use("landed", fields) == "off":
        return "full", "the audit turned the land verdict's reuse off (reuse_audit.py report)"
    url = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], text=True,
                         capture_output=True, check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    url = url.stdout
    if m := GITHUB.search(url.strip()):
        reuse_audit.enqueue("landed", fields, "green", slug=m.group(1), sha=sha)
    return "release", parent


def _sh(cmd: str, root: Path, label: str) -> int:
    """CMD in root as the timed row LABEL, without GOH_LAND_GATE: the key that chose this gate is no
    configuration of the gates it runs, and every GOH_* is in a proven step's cache identity
    (gates/_rust_proven.sh)."""
    os.environ.pop("GOH_LAND_GATE", None)
    return step_timings.timed(label, ["bash", "-c", cmd], cwd=root)


def run(root: Path, base: str, full: str, structural: str, runner: str) -> int:
    paths = changed(root, base)
    if paths is None or not release_only(paths):
        print(f"release gate: the diff since {base[:8]} is not release files only -- the full gate")
        sys.stdout.flush()
        return _sh(full, root, "release gate: the full gate")
    t0 = time.monotonic()  # the readers' graph is seconds of its own (8.7 s at load 125)
    tests = names(root, paths)
    step_timings.record(
        "release gate: choose the readers", (time.monotonic() - t0) * 1000, 0, "phase"
    )
    print(f"release gate: release files only since {base[:8]} -- structural + {len(tests)} tests")
    sys.stdout.flush()
    rc = _sh(structural, root, "release gate: structural")
    if rc != 0:
        return rc
    if not tests:
        return 0
    cmd = f"{runner} {' '.join(shlex.quote(t) for t in tests)}"
    return _sh(cmd, root, f"release gate: {len(tests)} test files")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    for verb in ("run", "decide", "names", "push-decide"):
        p = sub.add_parser(verb)
        p.add_argument("--root", type=Path, default=Path.cwd())
        if verb in ("run", "decide", "names"):
            p.add_argument("--base", required=True)
        if verb == "run":
            p.add_argument("--full", required=True)
            p.add_argument("--structural", default=STRUCTURAL)
            p.add_argument("--runner", default="")
    a = ap.parse_args(argv)
    root = a.root.resolve()
    if a.verb == "names":
        print("\n".join(names(root, changed(root, a.base) or [])))
        return 0
    if a.verb == "decide":
        print(decide(root, a.base))
        return 0
    if a.verb == "push-decide":
        print(" ".join(decide_push(root)))
        return 0
    return run(root, a.base, a.full, a.structural, a.runner or runner())


if __name__ == "__main__":
    sys.exit(main())
