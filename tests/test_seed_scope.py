"""Three seams in commit-scope seeded checks (GOH.SEEDSCOPE, found landing GOH.SEEDSTALE eae5f80).

* a renamed file's NEW path is judged at commit: every staged listing used `--diff-filter=ACM`, so
  `git mv a.py b.py` left b.py unread while its old path's store entry was settled away;
* a commit-scope run reads the TRACKED seed file from the index, not the worktree: an unstaged
  edit to the seed is not what the commit records;
* two commits settling one store file at once keep BOTH prunes: the write is read-modify-rename,
  and with no lock the second rename wrote back the first one's deleted entry.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from conftest import (
    REPO_ROOT,
    commit_all,
    git,
    hermetic_env,
    native_goh_path,
    run_goh,
    stage,
    write,
)

S = "(" + "s)"
HEDGE = f'print(f"{{n}} file{S}")\n'
SEED = "plural_hedge_seed.json"


def _root(repo: Path) -> str:
    return git(repo, "rev-list", "--max-parents=0", "HEAD").strip()


def _seed(root: str, *paths: str) -> str:
    entries = [
        {"root": root, "repo": "fixture", "path": p, "count": 1, "reason": "a test's"}
        for p in paths
    ]
    return json.dumps({"why": "a test's", "entries": entries}, indent=1) + "\n"


def _run(repo: Path, store: Path, *args: str) -> subprocess.CompletedProcess:
    return run_goh(repo, "plural-hedge", *args, env=hermetic_env(GOH_SEED_DIR=str(store)))


def test_a_renamed_file_is_judged_at_the_commit(repo: Path, tmp_path: Path) -> None:
    write(repo, "a.py", HEDGE)
    commit_all(repo)
    store = tmp_path / "store"
    store.mkdir()
    (store / SEED).write_text(_seed(_root(repo), "a.py"))
    git(repo, "mv", "a.py", "b.py")
    r = _run(repo, store, "--staged")
    assert r.returncode == 1, "b.py's hedge is unseeded and must be read: " + r.stdout + r.stderr
    assert "b.py" in r.stderr, r.stderr


def test_no_staged_listing_drops_renames() -> None:
    """The class: no tracked source lists staged files with a filter that leaves out R."""
    files = git(REPO_ROOT, "ls-files", "crates", "gates", "lib", "checks", "hooks", "tools").split()
    acm = "diff-filter=" + "ACM"
    hits = [
        f"{f}:{n}"
        for f in files
        if f.endswith((".rs", ".py", ".sh"))
        for n, line in enumerate((REPO_ROOT / f).read_text(errors="replace").splitlines(), 1)
        if acm in line and acm + "R" not in line
    ]
    assert hits == [], "a staged listing that skips a rename's new path: " + ", ".join(hits)


def test_the_commit_reads_the_tracked_seed_from_the_index(repo: Path, tmp_path: Path) -> None:
    write(repo, "a.py", HEDGE)
    commit_all(repo)
    write(repo, SEED, _seed(_root(repo), "a.py"))
    commit_all(repo, "seed")
    store = tmp_path / "no-store"
    # The worktree's seed loses the entry, unstaged; the commit records the index's, which holds it.
    write(repo, SEED, json.dumps({"why": "x", "entries": []}))
    write(repo, "a.py", HEDGE + "# touched\n")
    stage(repo, "a.py")
    r = _run(repo, store, "--staged")
    assert r.returncode == 0, "the index's seed holds a.py: " + r.stdout + r.stderr
    # And the other way: the worktree's seed holds an entry the index lacks.
    stage(repo, SEED)
    write(repo, SEED, _seed(_root(repo), "a.py"))
    r = _run(repo, store, "--staged")
    assert r.returncode == 1, "the index's seed holds nothing for a.py: " + r.stdout + r.stderr


def test_two_concurrent_commits_keep_both_prunes(repo: Path, tmp_path: Path) -> None:
    write(repo, "a.py", HEDGE)
    write(repo, "b.py", HEDGE)
    commit_all(repo)
    other = tmp_path / "other"
    shutil.copytree(repo, other, symlinks=True)
    store = tmp_path / "store"
    store.mkdir()
    (store / SEED).write_text(_seed(_root(repo), "a.py", "b.py"))
    write(repo, "a.py", "print(n)\n")
    stage(repo, "a.py")
    write(other, "b.py", "print(n)\n")
    stage(other, "b.py")
    # Each run holds the store between its read and its rename long enough for the other to read:
    # with no lock, the second rename writes back the entry the first deleted.
    env = hermetic_env(GOH_SEED_DIR=str(store), GOH_SEED_SETTLE_HOLD_MS="1500")
    procs = [
        subprocess.Popen(
            [str(native_goh_path()), "plural-hedge", "--staged"],
            cwd=d,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        for d in (repo, other)
    ]
    outs = [p.communicate(timeout=120) for p in procs]
    assert [p.returncode for p in procs] == [0, 0], outs
    left = json.loads((store / SEED).read_text())["entries"]
    assert left == [], f"both prunes kept: {left}"
