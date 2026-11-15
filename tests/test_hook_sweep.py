"""A goh release that changes a stock hook strands no consumer (GOH.HOOKSWEEP).

Every stock hook since 18330c7 (pre-commit) and 0ac0f70 (pre-push) decides nothing: it execs the
shared checkout's gates/commit_gate.sh or push_gate.sh, read at run time, so an old copy of it runs
today's gate. structural.sh still refused such a copy as "an OLDER stock hook" -- its own reason,
"the gate it runs is not today's", was false of it -- so each hook-body change turned every
consumer's next commit red, one repo at a time: after 0.107.0 (2026-11-12) five repos, each
fixed by hand. The refusal now holds only for a retired copy that does NOT delegate (it carries
gate logic of its own); a delegating one is named, with `goh hooks --refresh`, and passes.

The estate is three fake consumers, each on a real retired stock hook from history, plus the next
release simulated: today's stock retired in a copy of the list.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env, native_goh_path

# name -> (commit, hook): retired stock copies that delegate to the runtime gate, each a hook-body
# change that stranded consumers once it was retired untagged.
ESTATE = {"one": ("18330c7", "pre-commit"), "two": ("7c285e5", "pre-commit"),
          "three": ("97a4f9e", "pre-push")}  # fmt: skip


def _blob(commit: str, hook: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "-p", f"{commit}:hooks/{hook}"],
        capture_output=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout


def _consumer(root: Path, name: str, hook: str, body: bytes) -> Path:
    repo = root / name
    (repo / ".githooks").mkdir(parents=True)
    (repo / ".githooks" / hook).write_bytes(body)
    (repo / "a.py").write_text("x = 1\n")
    fast_init(repo)
    for args in (["config", "core.hooksPath", ".githooks"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=repo, check=True, stdin=subprocess.DEVNULL)
    return repo


def _structural(repo: Path) -> subprocess.CompletedProcess:
    env = hermetic_env()
    env["GOH_BIN"] = str(native_goh_path())
    return subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "structural.sh"), "--staged"],
        cwd=repo, env=env, capture_output=True, text=True, timeout=120,
        stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip


def _stale(repo: Path, goh_root: Path) -> list[str]:
    lib = REPO_ROOT / "gates" / "_hook_currency.sh"
    got = subprocess.run(
        ["bash", "-c", f'GOH_ROOT="{goh_root}"; . "{lib}"; goh_stale_hooks'],
        cwd=repo, env=hermetic_env(), capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL, check=True,
    )  # fmt: skip
    return got.stdout.split()


def _listed() -> str:
    return (REPO_ROOT / "retired_hooks.sha256").read_text(encoding="utf-8")


def test_the_retired_copies_are_retired_and_delegate():
    """The fixture means what it says: each copy is listed retired, and execs a runtime gate."""
    for commit, hook in ESTATE.values():
        body = _blob(commit, hook)
        assert f"{hashlib.sha256(body).hexdigest()}  {hook}" in _listed(), (commit, hook)
        assert b"_gate" in body and b"exec bash" in body, (commit, hook)


@pytest.mark.parametrize("name", sorted(ESTATE))
def test_no_consumer_on_a_retired_delegating_hook_is_refused(tmp_path, name):
    commit, hook = ESTATE[name]
    got = _structural(_consumer(tmp_path, name, hook, _blob(commit, hook)))
    text = got.stdout + got.stderr
    assert got.returncode == 0, text
    digest = hashlib.sha256(_blob(commit, hook)).hexdigest()
    entry = next(ln for ln in _listed().splitlines() if ln.startswith(digest))
    if "same-logic" not in entry:
        # Named, with the one command that brings it current -- never silent.
        assert "goh hooks --refresh" in text, text


def test_the_next_hook_change_strands_nobody(tmp_path):
    """Simulate the release after this one: today's stock is retired (appended untagged, as every
    body change is). A consumer on today's stock still passes."""
    goh = tmp_path / "goh"
    goh.mkdir()
    listed = _listed()
    for hook in ("pre-commit", "pre-push"):
        digest = hashlib.sha256((REPO_ROOT / "hooks" / hook).read_bytes()).hexdigest()
        listed += f"{digest}  {hook}  (the next release)\n"
    (goh / "retired_hooks.sha256").write_text(listed, encoding="utf-8")
    for i, hook in enumerate(("pre-commit", "pre-push", "pre-commit")):
        repo = _consumer(
            tmp_path / "estate", f"r{i}", hook, (REPO_ROOT / "hooks" / hook).read_bytes()
        )
        assert _stale(repo, goh) == [], repo


def test_a_retired_hook_with_its_own_gate_logic_is_still_refused(tmp_path):
    """The refusal keeps its teeth where its reason is true: 2fb2901's pre-commit ran the repo's
    gate itself, so an old copy of it does not run today's gate."""
    body = _blob("2fb2901", "pre-commit")
    assert b"commit_gate" not in body
    got = _structural(_consumer(tmp_path, "old", "pre-commit", body))
    text = got.stdout + got.stderr
    assert got.returncode == 1 and "OLDER stock hook" in text, text


def test_a_delegating_hook_is_judged_by_its_bytes_not_a_tag(tmp_path):
    """No human tag decides it: with every tag stripped from the retired list the verdicts hold,
    so a future body change cannot strand consumers through a forgotten `same-logic`."""
    goh = tmp_path / "goh"
    goh.mkdir()
    rows = [ln.split("(")[0] for ln in _listed().splitlines()]
    (goh / "retired_hooks.sha256").write_text("\n".join(rows) + "\n", encoding="utf-8")
    for name, (commit, hook) in ESTATE.items():
        repo = _consumer(tmp_path / "estate", name, hook, _blob(commit, hook))
        assert _stale(repo, goh) == [], name
