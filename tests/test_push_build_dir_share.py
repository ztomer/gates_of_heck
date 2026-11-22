"""A ship's push gate builds in the main checkout's own cargo build dir (GOH.BUILDDIRSHARE).

~/.cargo/config.toml keys the build dir by workspace path, so the push export
(`<export root>/<repo>-<hash>/<repo>`) kept a full second copy of every checkout it ever pushed:
routines 19+11 GB, divoomd 12+11, multitop 22+10 (DISK.BUILDGC, 2026-11-11). A `goh ship` push
(GOH_GATE_CLASS=ship) gates the main checkout's own HEAD under the land lock, and its install step
builds that same commit in the main checkout next: so the export takes the build dir cargo names for
the checkout, and the install is incremental. Only when the checkout is CLEAN at the pushed commit:
cargo judges a path crate fresh by mtime, so a build of other content into the checkout's build dir
would leave the checkout's older-mtime edits looking fresh. Any other push keeps its own dir.
"""

import json
import os
import subprocess
from pathlib import Path

from test_push_gate import REPO_ROOT, _git
from test_push_gate_runs import _cargo_repo, _push

SHARE = REPO_ROOT / "gates" / "_push_build_dirs.py"


def _cargo_build_dir(where: Path) -> str:
    out = subprocess.run(
        ["cargo", "metadata", "-q", "--format-version", "1", "--no-deps", "--offline"],
        cwd=where, capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
    ).stdout  # fmt: skip
    return json.loads(out)["build_directory"]


def _used(tmp_path: Path) -> str:
    return (tmp_path / "report.txt").read_text().splitlines()[-1].split("=", 1)[1]


def test_a_ship_push_builds_in_the_checkouts_own_build_dir(tmp_path, monkeypatch):
    repo = _cargo_repo(tmp_path)
    monkeypatch.setenv("GOH_GATE_CLASS", "ship")
    got = _push(repo, tmp_path, tmp_path / "exports")
    assert got.returncode == 0, got.stdout + got.stderr
    assert _used(tmp_path) == _cargo_build_dir(repo), "the ship's export built a second copy"


def test_a_push_nobody_classed_ship_keeps_its_own_build_dir(tmp_path, monkeypatch):
    repo = _cargo_repo(tmp_path)
    monkeypatch.delenv("GOH_GATE_CLASS", raising=False)
    got = _push(repo, tmp_path, tmp_path / "exports")
    assert got.returncode == 0, got.stdout + got.stderr
    assert _used(tmp_path) != _cargo_build_dir(repo)


def test_a_ship_push_of_a_dirty_checkout_keeps_its_own_build_dir(tmp_path, monkeypatch):
    repo = _cargo_repo(tmp_path)
    (repo / "src" / "lib.rs").write_text("pub fn edited() {}\n")  # uncommitted
    monkeypatch.setenv("GOH_GATE_CLASS", "ship")
    got = _push(repo, tmp_path, tmp_path / "exports")
    assert got.returncode == 0, got.stdout + got.stderr
    assert _used(tmp_path) != _cargo_build_dir(repo), "shared a build dir with a dirty checkout"


def test_an_edit_made_during_a_shared_build_is_made_newer_than_it(tmp_path):
    """The window the clean check cannot close: an edit landing in the checkout while the export
    builds is older than the export's output, so cargo would call it fresh. After the gate every
    tracked file that differs from HEAD gets the current time (only ever newer: never a stale
    file made to look fresh)."""
    repo = _cargo_repo(tmp_path)
    edited = repo / "src" / "lib.rs"
    edited.write_text("pub fn during() {}\n")
    old = 1_000_000_000  # 2001: any write since is later
    os.utime(edited, (old, old))
    clean = repo / "Cargo.toml"
    os.utime(clean, (old, old))
    subprocess.run(
        ["python3", str(SHARE), "--touch-changed", str(repo)], check=True, stdin=subprocess.DEVNULL
    )
    assert edited.stat().st_mtime > old + 10**8, "a changed file kept its old mtime"
    assert clean.stat().st_mtime == old, "an unchanged file was touched (it would rebuild)"


def test_the_share_is_refused_off_the_pushed_commit(tmp_path):
    repo = _cargo_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    ask = ["python3", str(SHARE), "--shared", str(repo)]
    on = subprocess.run(
        [*ask, head], capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL
    ).stdout.strip()
    off = subprocess.run(
        [*ask, "0" * 40], capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL
    ).stdout
    assert on == _cargo_build_dir(repo) and off == "", (on, off)
