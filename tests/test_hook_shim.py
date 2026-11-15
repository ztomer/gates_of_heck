"""A stock hook is a shim: a change to goh's hook logic reaches a consumer commit with NO refresh
(GOH.CLASS.ROLLOUT).

install.sh COPIES hooks into each consumer's tracked `.githooks`, so whatever logic a copy carried
went stale the moment goh moved: 33 repos ran stale stock hooks on 2026-11-15, and bringing them
current is a commit into trees other agents work in. The stock hook is now one POSIX shim, the
same bytes under every name, that finds the gates_of_heck checkout and runs its gates/hook.sh at
run time. These tests pin both halves: the shim holds no logic and never changes by accident (its
bytes are an ABI), and a goh whose hook.sh changes is what a consumer's next commit runs.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from _hook_goh import hook_goh
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

HOOKS = ("commit-msg", "post-rewrite", "pre-applypatch", "pre-commit", "pre-push")
# The shim's ABI. Changing the shim is the ONE change that needs every consumer refreshed (`goh
# hooks --refresh`), so it is deliberate: retire the old bytes in retired_hooks.sha256, then move
# this. A logic change goes in gates/hook.sh, which needs no refresh.
STOCK = {h: (REPO_ROOT / "hooks" / h).read_bytes() for h in HOOKS}
SHIM_SHA256 = "a79620f3b933041154cb48b763079a97f1b572224e313b14f1216e20a99a360f"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=False, env=hermetic_env(drop_git=True),
                          stdin=subprocess.DEVNULL)  # fmt: skip


def test_every_stock_hook_is_the_one_shim():
    names = sorted(p.name for p in (REPO_ROOT / "hooks").iterdir() if p.is_file())
    assert tuple(names) == HOOKS, names  # a new hook name is a new line here, on purpose
    bodies = {(REPO_ROOT / "hooks" / h).read_bytes() for h in HOOKS}
    assert len(bodies) == 1, "the stock hooks differ: a hook holds logic of its own"
    body = bodies.pop()
    assert body.startswith(b"#!/bin/sh\n"), "a shim is POSIX sh: the bash it needs is hook.sh's"
    assert hashlib.sha256(body).hexdigest() == SHIM_SHA256, (
        "the shim changed: that strands every consumer until a refresh. Put logic in "
        "gates/hook.sh; if the ABI must move, retire the old bytes and update SHIM_SHA256"
    )
    code = [ln for ln in body.decode().splitlines()[1:] if ln.strip() and not ln.startswith("#")]
    assert len(code) <= 8, code  # a search order and an error, nothing else


def test_gohs_own_hooks_are_the_shim():
    for h in HOOKS:
        assert (REPO_ROOT / ".githooks" / h).read_bytes() == (REPO_ROOT / "hooks" / h).read_bytes()


def _goh_copy(root: Path) -> Path:
    """A gates_of_heck holding the real hook.sh, tui/ and bash5 guard, and a commit gate that
    says it ran."""
    hook_goh(root)
    for g in ("commit_gate.sh", "commit_msg.sh", "push_gate.sh"):
        (root / "gates" / g).write_text(f'echo "{g} ran GOH_DIR=$GOH_DIR" >&2\n')
    return root


def _consumer(root: Path) -> Path:
    repo = root / "consumer"
    (repo / ".githooks").mkdir(parents=True)
    for h, body in STOCK.items():
        (repo / ".githooks" / h).write_bytes(body)
        (repo / ".githooks" / h).chmod(0o755)
    (repo / "f").write_text("x\n")
    fast_init(repo)
    for kv in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", ".githooks")):
        _git(repo, "config", *kv)
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-q", "--no-verify", "-m", "fixture").returncode == 0
    return repo


def _commit(repo: Path, goh: Path, n: int) -> subprocess.CompletedProcess:
    (repo / "f").write_text(f"{n}\n")
    env = hermetic_env(drop_git=True, GOH_DIR=str(goh), HOME=str(repo.parent / "home"))
    return subprocess.run(["git", "-C", str(repo), "commit", "-q", "-am", f"c{n}"],
                          capture_output=True, text=True, check=False, env=env,
                          stdin=subprocess.DEVNULL, timeout=patience(60))  # fmt: skip


def test_a_hook_logic_change_reaches_a_consumer_with_no_refresh(tmp_path):
    goh = _goh_copy(tmp_path / "goh")
    repo = _consumer(tmp_path)
    hooks_before = {h: (repo / ".githooks" / h).read_bytes() for h in HOOKS}
    first = _commit(repo, goh, 1)
    assert first.returncode == 0, first.stderr
    assert f"commit_gate.sh ran GOH_DIR={goh}" in first.stderr, first.stderr
    assert f"commit_msg.sh ran GOH_DIR={goh}" in first.stderr, first.stderr
    # goh's hook logic moves: one new line in gates/hook.sh, nothing in the consumer.
    hook_sh = goh / "gates" / "hook.sh"
    text = hook_sh.read_text()
    hook_sh.write_text(
        text.replace('case "$hook" in', 'echo "NEW HOOK LOGIC $hook" >&2\ncase "$hook" in', 1)
    )
    second = _commit(repo, goh, 2)
    assert second.returncode == 0, second.stderr
    assert "NEW HOOK LOGIC pre-commit" in second.stderr, second.stderr
    assert "NEW HOOK LOGIC commit-msg" in second.stderr, second.stderr
    assert {h: (repo / ".githooks" / h).read_bytes() for h in HOOKS} == hooks_before
    assert _git(repo, "status", "--porcelain").stdout == ""


@pytest.mark.parametrize("hook", HOOKS)
def test_with_no_goh_anywhere_the_shim_refuses_naming_the_fix(tmp_path, hook):
    repo = _consumer(tmp_path)
    env = hermetic_env(drop_git=True, GOH_DIR=str(tmp_path / "nowhere"), HOME=str(tmp_path))
    env.pop("GOH_LIVE_ROOT", None)
    r = subprocess.run(["sh", str(repo / ".githooks" / hook)], cwd=repo, capture_output=True,
                       text=True, check=False, env=env, stdin=subprocess.DEVNULL)  # fmt: skip
    assert r.returncode == 1, r.stderr
    assert f"no gates_of_heck at {tmp_path / 'nowhere'}" in r.stderr and "GOH_DIR" in r.stderr


def test_an_unknown_hook_name_is_refused_by_hook_sh(tmp_path):
    goh = _goh_copy(tmp_path / "goh")
    r = subprocess.run(["bash", str(goh / "gates" / "hook.sh"), ".githooks/pre-rebase"],
                       capture_output=True, text=True, check=False,
                       env=hermetic_env(GOH_DIR=str(goh)), stdin=subprocess.DEVNULL)  # fmt: skip
    assert r.returncode == 2 and "pre-rebase" in r.stderr, r.stderr
