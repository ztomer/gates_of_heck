"""Where an estate seed entry lives (GOH.SEEDPRIVATE, 2026-11-09).

gates_of_heck is PUBLIC, and its estate seeds (`release_quiet_seed.json`, `crates_behind_seed.json`
and the rest, `crates/goh/src/estate_seed.rs`) named the owner's private repositories:
`crates_behind_seed.json` published a private consumer's nested Cargo workspace and its count. A seed entry about a repository
now lives in THAT repository's own tracked seed file, or in the owner's private store
(`GOH_SEED_DIR`, else `$XDG_STATE_HOME/goh/seeds`), never in another repository's tree:

* a tracked entry whose root commit is not the judging repository's own is REFUSED -- the planted
  consumer entry this item exists to stop;
* the ratchet is the same from either home: at or under its count warns, over it or unseeded is
  red, and a stale entry (its file found clean by a full run) is red -- the repository that fixed
  the site deletes its own entry in the same commit, which it could not do in goh's tree;
* one entry in both homes is refused: one fact in one place.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, hermetic_env, run_goh, write

QUIET = "#!/usr/bin/env bash\nset -euo pipefail\ncargo test >/dev/null\n"
QUIET2 = QUIET + "./install.sh >/dev/null\n"
CLEAN = "#!/usr/bin/env bash\nset -euo pipefail\ncargo test\n"
FILE = "release_quiet_seed.json"
# Every seed keyed by root commit; the per-repo shrink-only seeds (shell lint, status markers)
# carry no root and are the judging repository's by construction.
ESTATE_SEEDS = (
    "changelog_tags_seed.json",
    "crates_behind_seed.json",
    "hook_index_seed.json",
    "mktemp_port_seed.json",
    "release_quiet_seed.json",
)


def _seed(root: str, count: int, path: str = "release.sh") -> str:
    entry = {"root": root, "repo": "fixture", "path": path, "count": count}
    return json.dumps({"why": "a test's", "entries": [{**entry, "reason": "unreviewed: a test"}]})


def _root(repo: Path) -> str:
    return git(repo, "rev-list", "--max-parents=0", "HEAD").strip()


def _quiet(repo: Path, store: Path) -> subprocess.CompletedProcess:
    return run_goh(repo, "release-quiet", env=hermetic_env(GOH_SEED_DIR=str(store)))


def test_a_planted_consumer_entry_in_a_tracked_seed_is_refused(repo: Path, tmp_path: Path) -> None:
    write(repo, "release.sh", QUIET)
    commit_all(repo)
    write(repo, FILE, _seed("bdbe3ad698c8f8591d4aed721cb761a6fddac09d", 1))
    commit_all(repo)
    r = _quiet(repo, tmp_path / "no-store")
    assert r.returncode != 0, r.stdout + r.stderr
    assert "another repository" in r.stderr and "bdbe3ad6" in r.stderr, r.stderr
    assert "GOH.SEEDPRIVATE" in r.stderr, r.stderr


def test_the_repos_own_tracked_entry_warns_and_over_its_count_is_red(
    repo: Path, tmp_path: Path
) -> None:
    write(repo, "release.sh", QUIET)
    commit_all(repo)
    store = tmp_path / "no-store"
    assert _quiet(repo, store).returncode == 1, "unseeded is red"
    write(repo, FILE, _seed(_root(repo), 1))
    commit_all(repo)
    r = _quiet(repo, store)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "seeded (GOH.RELHIDE ratchet): release.sh" in r.stderr, r.stderr
    write(repo, "release.sh", QUIET2)
    commit_all(repo)
    assert _quiet(repo, store).returncode == 1, "over its count is red"


def test_the_private_store_holds_a_repos_entry_with_the_same_ratchet(
    repo: Path, tmp_path: Path
) -> None:
    write(repo, "release.sh", QUIET2)
    commit_all(repo)
    store = tmp_path / "store"
    store.mkdir()
    (store / FILE).write_text(_seed(_root(repo), 2))
    r = _quiet(repo, store)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "seeded, not yet fixed" in r.stderr, r.stderr
    (store / FILE).write_text(_seed(_root(repo), 1))
    assert _quiet(repo, store).returncode == 1, "over its stored count is red"
    (store / FILE).write_text(_seed(_root(repo), 3))
    r = _quiet(repo, store)
    assert r.returncode == 0 and "lower its entry in " + str(store / FILE) in r.stderr, r.stderr


def test_a_stale_entry_is_red_in_either_home(repo: Path, tmp_path: Path) -> None:
    write(repo, "release.sh", CLEAN)
    commit_all(repo)
    store = tmp_path / "store"
    store.mkdir()
    (store / FILE).write_text(_seed(_root(repo), 1))
    r = _quiet(repo, store)
    assert r.returncode == 1 and "delete its entry from " + str(store / FILE) in r.stderr, r.stderr
    (store / FILE).unlink()
    write(repo, FILE, _seed(_root(repo), 1))
    commit_all(repo)
    r = _quiet(repo, store)
    assert r.returncode == 1 and "delete its entry from release_quiet_seed.json" in r.stderr, (
        r.stderr
    )


def test_one_entry_in_both_homes_is_refused(repo: Path, tmp_path: Path) -> None:
    write(repo, "release.sh", QUIET)
    commit_all(repo)
    write(repo, FILE, _seed(_root(repo), 1))
    commit_all(repo)
    store = tmp_path / "store"
    store.mkdir()
    (store / FILE).write_text(_seed(_root(repo), 1))
    r = _quiet(repo, store)
    assert r.returncode != 0 and "both" in r.stderr, r.stdout + r.stderr


def test_gates_of_heck_tracks_only_its_own_estate_entries() -> None:
    """The public tree names no other repository's seed entry (the published private nested workspace)."""
    own = _root(REPO_ROOT)
    tracked = set(git(REPO_ROOT, "ls-files").splitlines())
    for name in ESTATE_SEEDS:
        if name not in tracked:
            continue
        entries = json.loads((REPO_ROOT / name).read_text())["entries"]
        foreign = [f"{e['repo']} {e['path']}" for e in entries if e["root"] != own]
        assert not foreign, f"{name} names another repository's entries: {foreign}"


def test_a_clone_with_no_tags_calls_no_changelog_entry_stale(repo: Path, tmp_path: Path) -> None:
    """A CI checkout fetches no tags: changelog-tags read nothing to compare, so its entry is not
    stale there (it was red on gates_of_heck's own fresh clone)."""
    write(repo, "CHANGELOG.md", "# CHANGELOG\n\n## v0.1.0 -- first\n")
    commit_all(repo)
    entry = {"root": _root(repo), "repo": "fixture", "path": "CHANGELOG.md", "count": 1}
    seed = {"why": "a test's", "entries": [{**entry, "reason": "unreviewed: a test"}]}
    write(repo, "changelog_tags_seed.json", json.dumps(seed))
    commit_all(repo)
    env = hermetic_env(GOH_SEED_DIR=str(tmp_path / "no-store"))
    r = run_goh(repo, "changelog-tags", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    git(repo, "tag", "v0.1.0")
    r = run_goh(repo, "changelog-tags", env=env)
    assert r.returncode == 1 and "stale" in r.stderr, r.stdout + r.stderr
