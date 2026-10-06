"""Stock hooks are COPIED, so a stock change reaches no repo until it re-installs -- and says so.

Everything else in this estate delegates to the shared checkout at runtime; the two hooks are the
exception (install.sh copies them). Measured 2026-10-05: 14 repos still ran the pre-0ac0f70
pre-push, which gates the WORKING TREE instead of the pushed commit, and none of them knew.

Two halves: every stock hook ever shipped is in retired_hooks.sha256 (so install.sh upgrades it
without --force), and structural.sh -- which every generation of the pre-commit hook calls -- names
a hook that is an older stock, with the command that fixes it.
"""

import hashlib
import os
import subprocess

import pytest

from conftest import REPO_ROOT, hermetic_env


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, check=True
    ).stdout


def _shipped(name: str) -> dict[str, str]:
    """{sha256: commit} for every committed version of hooks/<name>."""
    out = {}
    for commit in _git("log", "--format=%h", "--", f"hooks/{name}").split():
        blob = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "cat-file", "-p", f"{commit}:hooks/{name}"],
            capture_output=True,
            check=True,
        ).stdout
        out[hashlib.sha256(blob).hexdigest()] = commit
    return out


@pytest.mark.parametrize("name", ["pre-commit", "pre-push"])
def test_every_stock_hook_ever_shipped_is_retired_or_current(name):
    current = hashlib.sha256((REPO_ROOT / "hooks" / name).read_bytes()).hexdigest()
    listed = (REPO_ROOT / "retired_hooks.sha256").read_text()
    missing = [
        f"{sha}  {name}  ({commit})"
        for sha, commit in _shipped(name).items()
        if sha != current and f"{sha}  {name}" not in listed
    ]
    assert not missing, "append to retired_hooks.sha256:\n" + "\n".join(missing)


def _repo_with_hook(tmp_path, body: bytes):
    repo = tmp_path / "r"
    (repo / ".githooks").mkdir(parents=True)
    (repo / ".githooks" / "pre-push").write_bytes(body)
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    return repo


def _structural(repo, native: bool):
    env = hermetic_env()
    env["GOH_DIR"] = str(REPO_ROOT)
    if not native:
        env["GOH_NO_NATIVE"] = "1"
    return subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "structural.sh"), "--staged"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("native", [False, True])
def test_an_older_stock_hook_is_named_with_its_fix(tmp_path, native):
    old = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "-p", "73391f0:hooks/pre-push"],
        capture_output=True,
        check=True,
    ).stdout
    got = _structural(_repo_with_hook(tmp_path, old), native)
    text = got.stdout + got.stderr
    assert "OLDER stock hook" in text and "install.sh" in text, text


def test_a_current_or_local_hook_is_not_named(tmp_path):
    for body in ((REPO_ROOT / "hooks" / "pre-push").read_bytes(), b"#!/bin/bash\nexit 0\n"):
        sub = tmp_path / hashlib.sha256(body).hexdigest()[:8]
        got = _structural(_repo_with_hook(sub, body), native=False)
        assert "OLDER stock hook" not in got.stdout + got.stderr
