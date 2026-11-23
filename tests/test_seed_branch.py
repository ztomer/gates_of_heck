"""A lane branch's settle waits for the branch the store is keyed to (GOH.SEEDBRANCH, 2026-11-22).

The private seed store (`~/.local/state/goh/seeds`) is ONE file per check for the whole machine,
keyed by repository root, not by branch. A commit's own run settled it at once (GOH.SEEDSTALE), so
a commit on a LANE branch that fixed seeded sites deleted their entries while main still carried
the sites, and every full run on main went red on them as new findings -- three times on
2026-11-22, the last holding a release's push. Now a settle made off the store's branch (the
branch `origin/HEAD` names, else `main`) is only LICENSED, under `<store>/deferred/`: the store is
untouched, a full run on the lane reads the licensed entry at what it finds, and the first full run
on the store's branch that finds the change applies it. Every test uses a scratch store.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import commit_all, git, hermetic_env, run_goh, stage, write

# A hedge assembled from pieces, which goh's own plural_hedge step does not read as one.
S = "(" + "s)"
ONE = f'print("x{S}")\n'
FILE = "plural_hedge_seed.json"


def _run(repo: Path, store: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    return run_goh(repo, *args, env=hermetic_env(GOH_SEED_DIR=str(store), **env))


def _seeded(repo: Path, tmp_path: Path) -> Path:
    write(repo, "a.py", ONE)
    commit_all(repo)
    root = git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    store = tmp_path / "store"
    store.mkdir()
    entry = {"root": root, "repo": "fixture", "path": "a.py", "count": 1, "reason": "a test's"}
    (store / FILE).write_text(json.dumps({"why": "a test's", "entries": [entry]}) + "\n")
    r = _run(repo, store, "plural-hedge")
    assert r.returncode == 0, f"the fixture's site is seeded: {r.stdout}{r.stderr}"
    return store


def _entries(store: Path) -> list[dict]:
    return json.loads((store / FILE).read_text())["entries"]


def _lane_fix(repo: Path, store: Path, hook: bool) -> subprocess.CompletedProcess:
    git(repo, "checkout", "-q", "-b", "lane")
    write(repo, "a.py", "print(n)\n")
    stage(repo, "a.py")
    if not hook:
        return _run(repo, store, "plural-hedge", "--staged")
    pending = store.parent / "pending"
    pending.mkdir()
    r = _run(repo, store, "plural-hedge", "--staged", GOH_SEED_PENDING=str(pending))
    assert r.returncode == 0, r.stdout + r.stderr
    return _run(repo, store, "seed-settle", str(pending), "--checkout", str(repo))


def _main_stays_green_then_settles(repo: Path, store: Path, said: str) -> None:
    assert len(_entries(store)) == 1, "a lane's settle leaves the store as it was: " + said
    assert "GOH.SEEDBRANCH" in said and "main" in said, "it says what it waits for: " + said
    commit_all(repo, "fix on the lane")
    lane = _run(repo, store, "plural-hedge")
    assert lane.returncode == 0, "the lane's full run reads the licence: " + lane.stderr
    assert len(_entries(store)) == 1, "a lane's full run applies nothing: " + lane.stderr
    git(repo, "checkout", "-q", "main")
    before = _run(repo, store, "plural-hedge")
    assert before.returncode == 0, "main, still holding the site, stays green: " + before.stderr
    assert len(_entries(store)) == 1
    git(repo, "merge", "-q", "--ff-only", "lane")
    after = _run(repo, store, "plural-hedge")
    assert after.returncode == 0, "the change on main settles, never red: " + after.stderr
    assert _entries(store) == [], "the licensed settle applies on main: " + after.stderr
    again = _run(repo, store, "plural-hedge")
    assert again.returncode == 0 and "stale" not in again.stderr, again.stderr
    assert not [p for p in (store / "deferred").rglob("*") if p.is_file()], "licence deleted"


def test_a_lane_commit_run_leaves_main_green_and_settles_when_it_reaches_main(
    repo: Path, tmp_path: Path
) -> None:
    store = _seeded(repo, tmp_path)
    r = _lane_fix(repo, store, hook=False)
    assert r.returncode == 0, r.stdout + r.stderr
    _main_stays_green_then_settles(repo, store, r.stderr)


def test_a_lane_commit_hooks_journal_is_licensed_not_applied(repo: Path, tmp_path: Path) -> None:
    store = _seeded(repo, tmp_path)
    r = _lane_fix(repo, store, hook=True)
    assert r.returncode == 0, r.stdout + r.stderr
    _main_stays_green_then_settles(repo, store, r.stdout + r.stderr)


def test_a_commit_on_main_still_settles_at_once(repo: Path, tmp_path: Path) -> None:
    store = _seeded(repo, tmp_path)
    write(repo, "a.py", "print(n)\n")
    stage(repo, "a.py")
    r = _run(repo, store, "plural-hedge", "--staged")
    assert r.returncode == 0 and _entries(store) == [], r.stderr


def test_an_unlicensed_stale_entry_on_main_is_still_red(repo: Path, tmp_path: Path) -> None:
    store = _seeded(repo, tmp_path)
    write(repo, "a.py", "print(n)\n")
    commit_all(repo, "fix with no commit-time run")
    push = _run(repo, store, "plural-hedge")
    assert push.returncode == 1 and "stale" in push.stderr, push.stdout + push.stderr
