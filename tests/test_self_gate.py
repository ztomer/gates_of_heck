"""A commit in a gates_of_heck checkout is judged by the tree it commits (GOH.SELFGATE).

RUFF.RULES (686548f) changed a gate's verdict over goh itself, and its worktree commit was judged by
the shared checkout's HEAD gates -- the gate it replaced -- so it landed only with
GOH_DIR=<worktree> GOH_LIVE=1. The first fix (756e6ae) handed the commit to the worktree LIVE and
broke goh's release: the version-bump commit met a binary built for the tree before the bump
(reverted, d7dc7dc). So the subject is the STAGED tree, exported, with a binary built FOR it.

The checkout is a real clone of this one, asked the way its stock hook asks: `bash <this
checkout>/gates/commit_gate.sh`, cwd in the clone. Its staged structural.sh prints a marker, and its
staged Cargo.toml is a version bump, the commit that broke the release. Its build script is a stub
that stores a fake goh stamped for the rev it was asked to build, so the test reads WHICH tree's
binary judged, with no cargo build.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from _todays_gates import _clean_checkout_of_todays_gates
from _tree_clone import run_head_cache
from conftest import REPO_ROOT, git, hermetic_env

MARK = "SELFGATE-STAGED-TREE-JUDGED"
FAKE = """#!/usr/bin/env bash
case "$1" in
    source-tree) echo "@STAMP@" ;;
    --version) echo "goh @VERSION@" ;;
    structural) [ "${2:-}" = --list-steps ] && grep -v -e '^#' -e '^ *$' "@STEPS@"; exit 0 ;;
esac
exit 0
"""
STUB = """#!/usr/bin/env bash
# the resolver's call for a pinned rev: build-goh.sh --rev REV
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
rev="$2"
echo "$rev" >> "@LOG@"
. "$root/gates/_goh_bin.sh"
stamp="$(goh_stamp_at "$root" "$rev")"  # the one formula, embedded inputs too (GOH.STAMPINPUTS)
version="$(bash "$root/lib/goh_version.sh" "$root" "$rev")"
dest="$(goh_store_path "$root" "$stamp")"
mkdir -p "$(dirname "$dest")"
steps="$root/crates/goh/structural_steps.txt"
sed -e "s|@STAMP@|$stamp|" -e "s|@VERSION@|$version|" -e "s|@STEPS@|$steps|" "@FAKE@" > "$dest"
chmod 755 "$dest"
"""


def _goh_clone(tmp_path: Path) -> tuple[Path, Path]:
    # goh AT THIS CHANGE, the tree under test, committed, as a landed goh would be.
    clone = _clean_checkout_of_todays_gates(tmp_path, rust=False)
    # A branch: main moves only through land.sh. Never "lane": a lane worktree holds lane/<ITEM>,
    # a ref that name cannot sit beside (GOH.REFRESHINDEXLOCK: this failed in every lane checkout).
    git(clone, "checkout", "-q", "-b", "scratch-branch")
    log = tmp_path / "built.log"
    fake = tmp_path / "fake-goh"
    fake.write_text(FAKE)
    stub = clone / "scripts" / "build-goh.sh"
    stub.write_text(STUB.replace("@LOG@", str(log)).replace("@FAKE@", str(fake)))
    git(clone, "update-index", "--assume-unchanged", "scripts/build-goh.sh")
    # The staged change: a gate edit that says it ran, and a version bump.
    gate = clone / "gates" / "structural.sh"
    lines = gate.read_text().splitlines(keepends=True)
    at = next(i for i, ln in enumerate(lines) if "goh_from_head" in ln) + 1
    lines.insert(at, f'echo "{MARK}" >&2\n')
    gate.write_text("".join(lines))
    manifest = clone / "Cargo.toml"
    text = manifest.read_text()
    old = next(ln for ln in text.splitlines() if ln.startswith("version = "))
    manifest.write_text(text.replace(old, 'version = "999.0.0"', 1))
    git(clone, "add", "gates/structural.sh", "Cargo.toml")
    # The working tree goes back: the marker is in the STAGED tree alone, so neither the clone's
    # HEAD nor its live tree can print it -- only a gate judging what is being committed.
    gate.write_text("".join(ln for ln in lines if MARK not in ln))
    return clone, log


def _commit_gate(clone: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = hermetic_env()  # GOH_LIVE=1, GOH_DIR=this checkout: the code under test
    env["GOH_BIN_STORE"] = str(tmp_path / "store")  # no other run's build answers
    env["GOH_HEAD_CACHE"] = str(run_head_cache())  # HEAD's is the run's; the staged tree's is new
    # The hand-off drops GOH_LIVE on purpose (its subject is an export, not a live tree); the
    # suite's "forgot GOH_LIVE" guard would refuse the clone's own live libs a gate sources there.
    env.pop("GATES_OF_HECK_SUITE", None)
    return subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "commit_gate.sh")],
        cwd=clone, env=env, capture_output=True, text=True, timeout=300,
        stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip


@pytest.mark.load_sensitive(
    reason="the commit gate it runs reads the process table under a 30 s cap (GOH.SELFGATEPS)"
)
def test_a_goh_commit_is_judged_by_its_staged_tree_and_that_trees_binary(tmp_path):
    clone, log = _goh_clone(tmp_path)
    tree = git(clone, "write-tree").strip()
    got = _commit_gate(clone, tmp_path)
    text = got.stdout + got.stderr
    assert MARK in text, text  # the staged gate judged, not this checkout's
    assert log.is_file() and tree in log.read_text().split(), text  # the binary built FOR it
    assert "999.0.0" not in text or "is not the build of" not in text, text
    assert got.returncode == 0, text


def test_a_consumer_commit_is_not_handed_anywhere(tmp_path):
    repo = tmp_path / "consumer"
    (repo / "gates").mkdir(parents=True)
    (repo / "gates" / "commit_gate.sh").write_text(f'echo "{MARK}"\n')  # a gate of its own
    (repo / "a.txt").write_text("a\n")
    fast_init(repo)
    git(repo, "add", "-A")
    got = _commit_gate(repo, tmp_path)
    assert MARK not in got.stdout + got.stderr, got.stdout + got.stderr
