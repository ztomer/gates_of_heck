"""Stock hooks are COPIED, so a stock change reaches no repo until it re-installs -- and says so.

Everything else in this estate delegates to the shared checkout at runtime; the two hooks are the
exception (install.sh copies them). Measured 2026-10-05: 14 repos still ran the pre-0ac0f70
pre-push, which gates the WORKING TREE instead of the pushed commit, and none of them knew.

Two halves: every stock hook ever shipped is in retired_hooks.sha256 (so install.sh upgrades it
without --force), and structural.sh -- which every generation of the pre-commit hook calls -- names
a hook that is an older stock, with the command that fixes it.
"""

import hashlib
import subprocess

import pytest
from conftest import REPO_ROOT, hermetic_env, native_goh_path


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout


def _shipped(name: str) -> dict[str, str]:
    """{sha256: commit} for every committed version of hooks/<name>."""
    out = {}
    for commit in _git("log", "--format=%h", "--", f"hooks/{name}").split():
        blob = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "cat-file", "-p", f"{commit}:hooks/{name}"],
            capture_output=True,
            check=True,
            stdin=subprocess.DEVNULL,
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
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, stdin=subprocess.DEVNULL)
    subprocess.run(
        ["git", "config", "core.hooksPath", ".githooks"],
        cwd=repo,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, stdin=subprocess.DEVNULL)
    return repo


def _structural(repo, native: bool):
    env = hermetic_env()
    env["GOH_DIR"] = str(REPO_ROOT)
    # Both values once chose a TIER; since Phase N3 both run the binary, named so no scratch
    # rebuild happens -- the parameter now reads as "the named binary" vs "the resolved one".
    if not native:
        env["GOH_BIN"] = str(native_goh_path())
    return subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "structural.sh"), "--staged"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
        check=False,
    )


@pytest.mark.parametrize("native", [False, True])
def test_an_older_stock_hook_is_named_with_its_fix(tmp_path, native):
    old = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "-p", "73391f0:hooks/pre-push"],
        capture_output=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout
    got = _structural(_repo_with_hook(tmp_path, old), native)
    text = got.stdout + got.stderr
    assert "OLDER stock hook" in text and "install.sh" in text, text
    # BACKLOG 6.6: named AND refused -- a warning printed on every commit for weeks changed nothing.
    assert got.returncode == 1, text


def test_a_current_or_local_hook_is_not_named(tmp_path):
    for body in ((REPO_ROOT / "hooks" / "pre-push").read_bytes(), b"#!/bin/bash\nexit 0\n"):
        sub = tmp_path / hashlib.sha256(body).hexdigest()[:8]
        got = _structural(_repo_with_hook(sub, body), native=False)
        assert "OLDER stock hook" not in got.stdout + got.stderr
        assert got.returncode == 0, got.stdout + got.stderr


# ── 6.19: a stock hook states what it does, never what the repo lacks ─────────


# A claim about the repo the hook is copied INTO, and only ever that. These read as claims about
# the host repo wherever they appear -- `no ci` is true or false of the repo, not of the hook.
_CLAIMS = ("no ci", "no workflow", "this repo has", "this repo lacks")
# `there is no` is ambiguous on its own: a hook may say "there is no rule THERE to run" about the
# shared checkout it delegates to (commit-msg does). So it is judged per sentence, and a sentence
# naming what the hook delegates to is the hook talking about itself.
_AMBIGUOUS = "there is no"
_SELF = ("$goh", "goh_dir", "gates_of_heck", "gates/", "checkout", "predecessor", "older")


def _sentences(text: str) -> list[str]:
    import re as _re

    return [s for s in _re.split(r"[.;\n]", text) if s.strip()]


def _claims_about_the_repo(text: str) -> list[str]:
    said = [c for c in _CLAIMS if c in text.lower()]
    for sentence in _sentences(text):
        low = sentence.lower()
        if _AMBIGUOUS in low and not any(s in low for s in _SELF):
            said.append(_AMBIGUOUS)
    return said


def test_no_stock_hook_makes_a_claim_about_the_repo():
    """The stock pre-push said "There is no CI behind this", false in every repo with a workflow
    (Adv360). A hook is copied into every repo; it can describe itself, not them."""
    for hook in sorted((REPO_ROOT / "hooks").iterdir()):
        if not hook.is_file():
            continue
        said = _claims_about_the_repo(hook.read_text(encoding="utf-8"))
        assert not said, f"hooks/{hook.name} claims {said} about the repo it is copied into"


def test_a_retired_hook_whose_logic_is_current_is_not_refused(tmp_path):
    """A header-only change retires the old bytes (install.sh must upgrade them without --force),
    but the hook it retires runs today's gate: refusing it would turn every consumer red over a
    comment. Such an entry is marked `same-logic`, and structural lets it pass."""
    lines = (REPO_ROOT / "retired_hooks.sha256").read_text(encoding="utf-8").splitlines()
    same = [line.split()[0] for line in lines if "same-logic" in line and " pre-push " in line]
    assert same, "no same-logic pre-push entry: the 6.19 header change retired nothing"
    old = None
    for commit in _git("log", "--format=%h", "--", "hooks/pre-push").split():
        blob = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "cat-file", "-p", f"{commit}:hooks/pre-push"],
            capture_output=True,
            check=True,
            stdin=subprocess.DEVNULL,
        ).stdout
        if hashlib.sha256(blob).hexdigest() in same:
            old = blob
            break
    assert old is not None, "the same-logic entry names no committed pre-push"
    got = _structural(_repo_with_hook(tmp_path, old), native=False)
    text = got.stdout + got.stderr
    assert "OLDER stock hook" not in text and got.returncode == 0, text
