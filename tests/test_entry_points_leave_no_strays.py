"""goh's land queue entry points leave nothing in a work tree (GOH.CLASS.CHECKOUT).

The class: goh's own locks, stages and temp files written INTO the checkouts it serves
(`bin/.build.lock`, `bin/goh.<pid>.staging`, a ship result beside the repo) read as "N uncommitted"
on a claim card and failed ships. tools/land.sh (lock and landed record in the common git dir) and
goh ship (result and logs under ~/.local/state or --out, its verify export under a temp root) are
run here end to end in a scratch repository with a bare origin and a lane worktree, and every
work tree must hold no file git does not track, ignored or not.

Part 2 of the class: every goh temp, lock and staging path comes from lib/run_path.sh, rooted
outside the work tree. Each entry point that helper serves is run here against a scratch repository
with GOH_RUN_DIR beside it, and each must leave the work tree empty of strays while the run root
holds what it wrote: a binary placement (lib/place.sh), the build-tree lock (lib/tree_lock.sh),
the proven cache's index tree inside a real `git commit -a` (gates/_proven.sh,
GOH.REFRESHINDEXLOCK), and the release kit's worktree (tools/release_goh.sh). build-goh.sh's
lock is run for real, killed mid-build, in tests/test_build_lock_ignored.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import test_ship
from conftest import REPO_ROOT, git, hermetic_env
from test_ship import SHIP, _env

shipped = test_ship.shipped  # the fixture, by assignment
LAND = REPO_ROOT / "tools" / "land.sh"


def _strays(tree: Path) -> list[str]:
    out = git(tree, "status", "--porcelain", "--untracked-files=all", "--ignored")
    return [ln for ln in out.splitlines() if ln.strip()]


def test_land_then_ship_leave_no_file_in_any_work_tree(shipped: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt-lane"
    git(shipped, "worktree", "add", "-q", "-b", "lane", str(wt))
    (wt / "lane.txt").write_text("lane\n")
    git(wt, "add", "lane.txt")
    git(wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify", "-m",
        "feat: lane")  # fmt: skip
    land = subprocess.run(["bash", str(LAND)], cwd=wt, capture_output=True, text=True, timeout=120,
                          env=hermetic_env(drop_git=True, GOH_LAND_GATE="true"),
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert land.returncode == 0, land.stdout + land.stderr
    ship = subprocess.run([sys.executable, str(SHIP), "--repo", str(shipped), "--wait"],
                          env=_env(tmp_path, HOME=str(tmp_path / "home")), capture_output=True,
                          text=True, timeout=120, stdin=subprocess.DEVNULL,
                          check=False)  # fmt: skip
    assert ship.returncode == 0, ship.stdout + ship.stderr
    assert {"main": _strays(shipped), "lane": _strays(wt)} == {"main": [], "lane": []}


def _bash(script: str, cwd: Path, run: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "-c", script], cwd=cwd, capture_output=True, text=True,
                          env=hermetic_env(drop_git=True, GOH_RUN_DIR=str(run), **extra),
                          timeout=120, stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def test_a_placement_stages_outside_the_tree(repo: Path, tmp_path: Path) -> None:
    run = tmp_path / "run"
    src = tmp_path / "built"
    src.write_bytes(b"whole")
    place = REPO_ROOT / "lib" / "place.sh"
    done = _bash(f'. "{place}" && goh_place "{src}" "$PWD/bin/goh"', repo, run)
    assert done.returncode == 0, done.stderr
    assert _strays(repo) == ["?? bin/goh"]  # the destination itself, and nothing beside it
    assert [p.name for p in (repo / "bin").iterdir()] == ["goh"]
    assert list(run.iterdir()), "the stage's directory is under the run root"


def test_the_tree_lock_is_taken_outside_the_tree(repo: Path, tmp_path: Path) -> None:
    run = tmp_path / "run"
    lib = REPO_ROOT / "lib" / "tree_lock.sh"
    tui = REPO_ROOT / "tui" / "lib.sh"
    done = _bash(f'. "{tui}"; . "{lib}"; tree_lock_acquire "$PWD" "probe"', repo, run)
    assert done.returncode == 0, done.stdout + done.stderr
    assert _strays(repo) == []
    assert len(list(run.glob("*/tree.lock"))) == 1


HOOK = """#!/usr/bin/env bash
. "{goh}/gates/_git_env.sh"
. "{goh}/gates/_proven.sh"
goh_hook_unbind
test -e "$(git rev-parse --git-dir)/index.lock" || {{ echo "no index.lock held"; exit 1; }}
proven_tree > "{out}" || {{ echo "proven_tree failed"; exit 1; }}
"""


def test_the_proven_index_tree_answers_while_another_git_holds_index_lock(
    repo: Path, tmp_path: Path
) -> None:
    """A gate keying the proven cache in a SHARED checkout, while another session's git holds
    .git/index.lock: proven_tree's `git write-tree` on the default index took that lock and failed
    ("Unable to create .git/index.lock: File exists", the GOH.REFRESHINDEXLOCK class). Through the
    helper it writes from a private copy, answers, and leaves the lock alone."""
    want = git(repo, "write-tree").strip()
    lock = repo / ".git" / "index.lock"
    lock.write_text("")  # another git's
    try:
        done = _bash(f'. "{REPO_ROOT}/gates/_proven.sh"; proven_tree', repo, tmp_path / "run")
        assert done.returncode == 0, done.stdout + done.stderr
        assert done.stdout.strip() == want
        assert lock.read_text() == "", "the other git's lock was left as it was"
    finally:
        lock.unlink()
    assert _strays(repo) == []


def test_the_proven_index_tree_inside_a_commit_reads_the_hook_index(
    repo: Path, tmp_path: Path
) -> None:
    """`git commit -a` holds .git/index.lock while its hook runs, and the index it commits is
    .git/index.lock itself: through the helper, proven_tree inside the hook names the tree the
    commit records (the binding, gates/_git_env.sh, still decides WHICH index is copied)."""
    out = tmp_path / "tree"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(HOOK.format(goh=REPO_ROOT, out=out))
    hook.chmod(0o755)
    tracked = git(repo, "ls-files").splitlines()[0]
    (repo / tracked).write_text("changed by the commit\n")
    env = hermetic_env(drop_git=True, GOH_RUN_DIR=str(tmp_path / "run"))
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-a",
                           "-m", "x"], cwd=repo, env=env, capture_output=True, text=True,
                          timeout=120, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    assert out.read_text().strip() == git(repo, "rev-parse", "HEAD^{tree}").strip()
    assert _strays(repo) == []


LAND_STUB = """#!/usr/bin/env bash
pwd -P > "{out}"
git status --porcelain --untracked-files=all --ignored >> "{out}"
exit 1
"""


def test_the_release_worktree_lives_under_the_run_root(repo: Path, tmp_path: Path) -> None:
    """The kit's worktree was build/release-vX inside the checkout. Its land step is a stub that
    records where it ran and refuses, so the kit stops there and its trap removes the worktree."""
    out = tmp_path / "land-ran-in"
    (repo / "tools").mkdir()
    (repo / "tools" / "land.sh").write_text(LAND_STUB.format(out=out))
    (repo / "tools" / "land.sh").chmod(0o755)
    (repo / "CHANGELOG.md").write_text(
        "# CHANGELOG\n\n## Unreleased\n\n* **Two.** b\n\n## v0.1.0 -- first\n\n* **One.** a\n"
    )
    (repo / "Cargo.toml").write_text('[workspace.package]\nversion = "0.1.0"\n')
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "feat: x")
    run = tmp_path / "run"
    kit = REPO_ROOT / "tools" / "release_goh.sh"
    for _ in range(2):  # twice: the second finds the one stable path free again
        done = _bash(f'exec bash "{kit}" 0.2.0', repo, run)
        assert done.returncode != 0, done.stdout + done.stderr
        ran_in = out.read_text().splitlines()[0]
        assert ran_in.startswith(os.path.realpath(run)), ran_in
        assert ran_in.endswith("/release"), ran_in
        assert _strays(repo) == []
        assert len(git(repo, "worktree", "list").splitlines()) == 1, "the trap removed the worktree"
