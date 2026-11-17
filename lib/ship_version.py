"""`goh ship`'s version and git plumbing (split from tools/ship.py by concern, GOH.CAPHEADROOM): the
newest release tag, the next version (git-cliff's answer, or --version / --bump), and the
environment a step runs in without git's repository bindings. Pinned by tests/test_ship*.py."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import gitrun

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def git_bindings() -> tuple[str, ...]:
    """The variables that bind git to one repository: git's own list (`--local-env-vars`,
    checks/_gitutil.py), never a hand copy -- the copy here held 6 of git's 15 (GOH.ENVLEAK)."""
    sys.path.insert(0, str(ROOT / "checks"))
    from _gitutil import local_env_vars

    return local_env_vars()


class Refused(Exception):
    pass


def git(repo: Path, *args: str) -> str:
    done = gitrun.run_reader(args, repo=repo, timeout=60)
    return done.stdout.strip() if done.returncode == 0 else ""


def unbound_env() -> dict[str, str]:
    """This environment without git's repository bindings: a step runs in the shipped repo, never in
    whatever repository a hook that started `goh ship` had bound (contract #12)."""
    return {k: v for k, v in os.environ.items() if k not in git_bindings()}


def newest_tag(repo: Path) -> str:
    for tag in git(repo, "tag", "--list", "v*", "--sort=-v:refname").splitlines():
        if SEMVER.match(tag):
            return tag
    return ""


CLIFF_TAGS = r"^v[0-9]+\.[0-9]+\.[0-9]+$"  # SEMVER, as git-cliff's --tag-pattern


def cliff_version(repo: Path, tag: str) -> str:
    """git-cliff's next version after TAG (X.Y.Z), or "" when it finds nothing to bump."""
    if not shutil.which("git-cliff"):
        raise Refused("the next version is git-cliff's answer and git-cliff is not on PATH: "
                      "brew install git-cliff (or pass --version X.Y.Z / --bump KIND)")  # fmt: skip
    done = subprocess.run(["git-cliff", "--bumped-version", "--tag-pattern", CLIFF_TAGS],
                          cwd=repo, env=unbound_env(), capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=False, timeout=120)  # fmt: skip
    lines = done.stdout.split()
    if done.returncode != 0 or not lines or not SEMVER.match(lines[-1]):
        why = (done.stderr.strip().splitlines() or [f"exit {done.returncode}"])[-1]
        raise Refused(f"git-cliff --bumped-version gave no vX.Y.Z: {why}")
    return "" if lines[-1] == tag else lines[-1].removeprefix("v")


def next_version(repo: Path, tag: str, version: str | None, bump: str | None,
                 releases: bool = True) -> str:  # fmt: skip
    if version:
        if bump:
            raise Refused(f"--version {version} and --bump {bump} both name the version: pass one")
        if not re.fullmatch(r"\d+\.\d+\.\d+", version.removeprefix("v")):
            raise Refused(f"--version {version!r} is not X.Y.Z")
        return version.removeprefix("v")
    if bump == "none":
        held = [t for t in git(repo, "tag", "--points-at", "HEAD").splitlines() if SEMVER.match(t)]
        if tag and not held:
            raise Refused(f"--bump none ships no release, but {repo.name} tags releases and HEAD "
                          f"is past {tag}, so it would land untagged: pass --version X.Y.Z for "
                          "the version a commit already bumped (the kit tags it), or --bump "
                          "patch|minor|major to cut the next")  # fmt: skip
        return ""
    m = SEMVER.match(tag)
    if not m:  # a first release names its version: nothing to bump from (GOH.FIRSTTAG)
        if releases:
            raise Refused(f"{repo.name} has no v-tag, so there is no version to bump: pass "
                          "--version X.Y.Z for its first release (or --bump none)")  # fmt: skip
        return ""
    major, minor, patch = (int(x) for x in m.groups())
    if not bump:
        found = cliff_version(repo, tag)
        if found:
            return found
        bump = "patch"
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"
