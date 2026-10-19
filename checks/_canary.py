"""Run a shared gate against a tree that violates it and one that does not; find gates no case drives.

The engine half of `_canary_cases.py`, moved here from game_asset_factory's
`tools/check_gate_canary.py` on 2026-10-19 so a gate, its case, and the check that the case exists
all land in one commit (see `_canary_cases.py` for the history). Two callers, one implementation:
`tests/test_canary.py` here, and GAF's canary, which imports these rather than keep a copy.

THE FIXTURE MUST BE IN SCOPE. A gate that scans nothing exits 0 and looks exactly like a gate that
scanned everything and found nothing: `no_allow` reported "0 files clean" on a root-level `.rs` it
never reads, and `tests_registered` passed an unregistered file not named `test*`. Each time the
gate was fine and the fixture was out of scope, which is why a case is worth nothing unless the
violating tree goes RED -- a red is only possible if the check read the file.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _canary_cases import CASES, UNCOVERED  # noqa: E402
from _gitutil import foreign_repo_env, scratch_git  # noqa: E402

CHECKS = os.path.dirname(os.path.abspath(__file__))


def build_repo(root: str, files: dict) -> str:
    """A throwaway git repo at `root` with `files` TRACKED -- these gates police tracked content.

    `{root}` in a file's CONTENT becomes the fixture's absolute path: a gate whose evidence is a
    diagnostic naming an absolute path (check_swift_warnings asks whether `/Users/.../App.swift`
    is inside the repo it judges) cannot be fixtured with a static literal that cannot know it.
    """
    os.makedirs(root, exist_ok=True)
    scratch_git(root)
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content.replace("{root}", root))
    if files:
        subprocess.run(
            ["git", "-C", root, "add", "-f", "--", *files],
            capture_output=True,
            check=True,
            env=foreign_repo_env(),
            stdin=subprocess.DEVNULL,
        )
    return root


def run_check(repo: str, script: str, argv: list, checks_dir: str = CHECKS) -> int | None:
    """The gate's exit code with `repo` as cwd, or None when no such gate is installed.

    Dispatched on extension: a `.sh` handed to python3 exits non-zero on line one, which LOOKS like
    a gate biting. The child runs in the fixture under `foreign_repo_env`, or a hook's GIT_DIR
    would make the gate's own git calls answer about the real repository.
    """
    path = os.path.join(checks_dir, script)
    if not os.path.isfile(path):
        return None
    runner = ["bash"] if script.endswith(".sh") else [sys.executable]
    return subprocess.run(
        [*runner, path, *argv],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env=foreign_repo_env(),
    ).returncode


def run_case(case, workdir: str, index: int, checks_dir: str = CHECKS):
    """(verdict, message): True when RED on the violating tree and GREEN on the clean one.

    None when the gate is not installed. An `argv` of None marks a flag-driven case: both
    directions run over the same trivial tree, and the two "trees" are the two argvs.
    """
    label, script, argv, violating, clean = case
    results = []
    for name, spec in (("violating", violating), ("clean", clean)):
        if argv is None:
            repo = build_repo(os.path.join(workdir, f"{index}_{name}"), {"f.txt": "x\n"})
            results.append(run_check(repo, script, spec, checks_dir))
        else:
            repo = build_repo(os.path.join(workdir, f"{index}_{name}"), spec)
            results.append(run_check(repo, script, argv, checks_dir))
    got_bad, got_clean = results
    if got_bad is None:
        return None, f"{label}: {script} not present at {checks_dir}"
    if got_bad == 0:
        return False, (
            f"{label}: the VIOLATING tree passed -- the gate did not bite, or the "
            f"fixture is out of its scope"
        )
    if got_clean != 0:
        return False, f"{label}: the CLEAN tree failed (exit {got_clean}) -- false positive"
    return True, f"{label}: red on a violation, green on a clean tree"


@dataclass
class Coverage:
    """Which installed gates nothing drives, and which entries name a gate that is gone."""

    unreached: list = field(default_factory=list)
    stale: list = field(default_factory=list)
    empty_scope: bool = False

    @property
    def ok(self) -> bool:
        return not (self.unreached or self.stale or self.empty_scope)

    def problems(self, noun: str = "shared gate") -> list:
        if self.empty_scope:
            return [f"no {noun}s found -- the scan looked at nothing, which is not a pass"]
        out = [
            f"{name}: a {noun} no case drives and no exemption names -- add its case to "
            f"checks/_canary_cases.py, or an UNCOVERED reason, in the commit that adds it"
            for name, _ in self.unreached
        ]
        out += [
            f"{name}: {why} -- names no installed {noun}; delete it" for name, why in self.stale
        ]
        return out


def uncanaried(checks_dir: str | None = None, cases=None, uncovered=None) -> Coverage:
    """Installed gates (`check_*.py|.sh`) that no case drives and no exemption names.

    Two-way: a case or exemption naming a gate that is not installed is STALE, because beside the
    gates such an entry can only be a deleted gate's leftover, and it is how the count would start
    lying in the other direction. An empty directory is a failure, never a pass.
    """
    checks_dir = checks_dir or CHECKS
    cases = CASES if cases is None else cases
    uncovered = UNCOVERED if uncovered is None else uncovered
    declared = sorted(
        name
        for name in os.listdir(checks_dir)
        if name.startswith("check_") and name.endswith((".py", ".sh"))
    )
    if not declared:
        return Coverage(empty_scope=True)
    driven = {case[1] for case in cases}
    named = driven | set(uncovered)
    return Coverage(
        unreached=[(name, checks_dir) for name in declared if name not in named],
        stale=sorted(
            [(name, "a case drives it") for name in driven if name not in declared]
            + [(name, why) for name, why in uncovered.items() if name not in declared]
        ),
    )
