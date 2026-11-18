"""lib/identity_cache.py: gate_identity's listing and build inputs, asked once per state.

GOH.IDENTITYPORT: one land gate ran 2,180 `git ls-files` and 712 `goh_version.sh --inputs`, each
process asking afresh. The memo must (1) spare the spawn on a second process asking of the same
state and (2) never answer stale: every change that moves ls-files' answer moves the key.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_identity
import identity_cache


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   stdin=subprocess.DEVNULL)  # fmt: skip


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GOH_IDENTITY_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "repo"
    root.mkdir()
    fast_init(root)
    for rel in ("lib/a.py", "lib/sub/b.py", "gates/g.sh", "crates/x/src/main.rs", "Cargo.toml",
                "docs/no.md"):  # fmt: skip
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(rel)
    (root / ".gitignore").write_text("*.log\n")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one")
    return root


def _spawns(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []
    real = gate_identity.subprocess.run

    def run(argv, *a, **k):
        seen.append(list(argv))
        return real(argv, *a, **k)

    monkeypatch.setattr(gate_identity.subprocess, "run", run)
    return seen


def _truth(root: Path, parts: tuple[str, ...]) -> list[str]:
    got = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "-co", "--exclude-standard", "--", *parts],
        capture_output=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.decode()
    got = {r for r in got.split("\0") if r}
    return sorted(r for r in got if os.path.lexists(root / r))


def test_a_second_ask_of_the_same_state_spawns_nothing(checkout: Path, monkeypatch) -> None:
    seen = _spawns(monkeypatch)
    first = gate_identity._files(checkout)
    gate_identity._files(checkout, ("crates",))
    gate_identity._files(checkout, ("lib/a.py",))
    assert gate_identity._files(checkout) == first
    assert sum("ls-files" in a for a in seen) == 1
    gate_identity.build_inputs(checkout)
    gate_identity.build_inputs(checkout)
    assert sum("--inputs" in a for a in seen) == 1


@pytest.mark.parametrize("parts", [gate_identity.PARTS, ("crates",), ("lib/sub",), ("lib/a.py",)])
def test_every_change_to_the_listing_moves_the_answer(checkout: Path, parts) -> None:
    def same() -> None:
        assert gate_identity._files(checkout, parts) == _truth(checkout, parts)

    same()
    (checkout / "lib" / "sub" / "new.py").write_text("untracked")
    same()
    (checkout / "lib" / "a.py").unlink()
    same()
    (checkout / "lib" / "a.py").write_text("back")
    (checkout / "crates" / "x" / "src" / "keep.log").write_text("ignored")
    same()
    (checkout / ".gitignore").write_text("*.log\nnew.py\n")
    same()
    (checkout / "lib" / "sub" / ".gitignore").write_text("b.py\n")
    same()
    (checkout / "crates" / "x" / "src" / ".gitignore").write_text("")
    _git(checkout, "rm", "-q", "--cached", "gates/g.sh")
    same()
    _git(checkout, "add", "-f", "crates/x/src/keep.log")  # the index alone moves the answer
    same()
    (checkout / "crates" / "x" / "src" / "new.rs").write_text("untracked")
    same()
    (checkout / ".git" / "info").mkdir(exist_ok=True)
    (checkout / ".git" / "info" / "exclude").write_text("*.rs\n")
    same()


def test_build_inputs_follow_the_commit(checkout: Path) -> None:
    assert gate_identity.build_inputs(checkout) == ["crates", "Cargo.toml", "Cargo.lock",
                                                    "rust-toolchain.toml"]  # fmt: skip
    (checkout / "lib" / "emb.json").write_text("{}")
    (checkout / "crates" / "x" / "src" / "main.rs").write_text(
        'include_str!("../../../lib/emb.json");'
    )
    _git(checkout, "add", "-A")
    _git(checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "two")
    assert gate_identity.build_inputs(checkout)[-1] == "lib/emb.json"
    _git(checkout, "pack-refs", "--all")
    assert identity_cache.head_commit(checkout) == subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], capture_output=True, text=True,
        check=True, stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip
    _git(checkout, "checkout", "-q", "--detach", "HEAD~1")
    assert gate_identity.build_inputs(checkout)[-1] == "rust-toolchain.toml"
