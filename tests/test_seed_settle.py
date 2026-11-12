"""A commit's own run settles the seed entries its change settles (GOH.SEEDSTALE, 2026-11-12).

An estate-seeded check (`crates/goh/src/estate_seed.rs`) keys an entry by root commit and file, in
the repository's tracked seed file or in the owner's PRIVATE store. Five times on 2026-11-11/12 an
agent fixed a seeded site, the commit passed pre-commit, and the PUSH went red: the full run found
the store entry stale, and nothing in a commit can delete a file in no tree -- a coordinator
hand-edited the store each time. Now:

* a commit whose staged tree removes the last finding a STORE entry holds deletes that entry
  itself (or lowers it), and says so -- the push that follows finds nothing stale;
* growth is still refused, and a refused commit settles nothing;
* a TRACKED entry the commit leaves with nothing to hold is red AT the commit, which can delete it,
  rather than at the push;
* the push's stale check stays as the backstop.

Every test uses a scratch store (`GOH_SEED_DIR` under `tmp_path`), never the machine's.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from conftest import commit_all, git, hermetic_env, run_goh, stage, write

# A hedge assembled from pieces, which goh's own plural_hedge step does not read as one: this file
# plants hedges in fixtures, and its own tree holds none.
S = "(" + "s)"
HEDGE = f'print(f"{{n}} step{S} ran")\n'
HEDGE2 = HEDGE + f'print(f"{{n}} file{S}")\n'
ONE = f'print("x{S}")\n'
# The same for a planted temp: `tests/test_temp_claimable.py` reads every test's `$(mktemp ...)`.
MK = "mk" + "temp"

# One seeded check per row: its command, its seed file, a file with ONE finding, the same file with
# TWO, and the fixed file. plural_hedge, mktemp_port and release_quiet share `estate_seed::verdict`.
CHECKS = [
    pytest.param(
        "plural-hedge",
        "plural_hedge_seed.json",
        "a.py",
        HEDGE,
        HEDGE2,
        "print(n)\n",
        id="plural_hedge",
    ),
    pytest.param(
        "mktemp-portable",
        "mktemp_port_seed.json",
        "a.sh",
        f"#!/usr/bin/env bash\nd=$({MK} -d -t goh)\n",
        f"#!/usr/bin/env bash\nd=$({MK} -d -t goh)\ne=$({MK} -d -t goh2)\n",
        f"#!/usr/bin/env bash\nd=$({MK} -d -t goh-x.XXXXXX)\n",
        id="mktemp_port",
    ),
    pytest.param(
        "release-quiet",
        "release_quiet_seed.json",
        "release.sh",
        "#!/usr/bin/env bash\nset -euo pipefail\ncargo test >/dev/null\n",
        "#!/usr/bin/env bash\nset -euo pipefail\ncargo test >/dev/null\n./install.sh >/dev/null\n",
        "#!/usr/bin/env bash\nset -euo pipefail\ncargo test\n",
        id="release_quiet",
    ),
]


def _root(repo: Path) -> str:
    return git(repo, "rev-list", "--max-parents=0", "HEAD").strip()


def _seed(root: str, path: str, count: int) -> str:
    entry = {"root": root, "repo": "fixture", "path": path, "count": count, "reason": "a test's"}
    return json.dumps({"why": "a test's", "entries": [entry]}, indent=1) + "\n"


def _run(repo: Path, store: Path, cmd: str, *args: str) -> subprocess.CompletedProcess:
    return run_goh(repo, cmd, *args, env=hermetic_env(GOH_SEED_DIR=str(store)))


def _stored(store: Path, file: str) -> list[dict]:
    return json.loads((store / file).read_text())["entries"]


def _seeded(repo: Path, tmp_path: Path, cmd: str, file: str, rel: str, one: str, count: int = 1):
    write(repo, rel, one)
    commit_all(repo)
    store = tmp_path / "store"
    store.mkdir()
    (store / file).write_text(_seed(_root(repo), rel, count))
    r = _run(repo, store, cmd)
    assert r.returncode == 0, f"the fixture's site is seeded: {r.stdout}{r.stderr}"
    return store


@pytest.mark.parametrize(("cmd", "file", "rel", "one", "two", "fixed"), CHECKS)
def test_a_fix_committed_leaves_no_stale_store_entry_for_the_push(
    repo: Path, tmp_path: Path, cmd: str, file: str, rel: str, one: str, two: str, fixed: str
) -> None:
    store = _seeded(repo, tmp_path, cmd, file, rel, one)
    write(repo, rel, fixed)
    stage(repo, rel)
    commit_run = _run(repo, store, cmd, "--staged")
    assert commit_run.returncode == 0, commit_run.stdout + commit_run.stderr
    assert "GOH.SEEDSTALE" in commit_run.stderr and "entry deleted" in commit_run.stderr, (
        "the commit says it settled the entry: " + commit_run.stderr
    )
    assert _stored(store, file) == [], "the commit's own run deleted the entry it settled"
    commit_all(repo, "fix")
    push = _run(repo, store, cmd)
    assert push.returncode == 0, "the push finds nothing stale: " + push.stdout + push.stderr
    assert "stale" not in push.stderr, push.stderr


@pytest.mark.parametrize(("cmd", "file", "rel", "one", "two", "fixed"), CHECKS)
def test_growth_is_still_refused_and_a_refused_commit_settles_nothing(
    repo: Path, tmp_path: Path, cmd: str, file: str, rel: str, one: str, two: str, fixed: str
) -> None:
    store = _seeded(repo, tmp_path, cmd, file, rel, one)
    before = (store / file).read_text()
    write(repo, rel, two)
    stage(repo, rel)
    grown = _run(repo, store, cmd, "--staged")
    assert grown.returncode == 1, "over its count is red at the commit: " + grown.stderr
    assert (store / file).read_text() == before, "growth never touches the store"
    # A refused commit settles nothing, though another of its files would have settled.
    write(repo, rel, fixed)
    write(repo, "z" + rel, one)
    stage(repo, rel, "z" + rel)
    refused = _run(repo, store, cmd, "--staged")
    assert refused.returncode == 1, "an unseeded file is red: " + refused.stderr
    assert (store / file).read_text() == before, "a refused commit settles nothing"


def test_a_partial_fix_lowers_the_store_entry(repo: Path, tmp_path: Path) -> None:
    two = HEDGE2
    store = _seeded(repo, tmp_path, "plural-hedge", "plural_hedge_seed.json", "a.py", two, 2)
    write(repo, "a.py", HEDGE)
    stage(repo, "a.py")
    r = _run(repo, store, "plural-hedge", "--staged")
    assert r.returncode == 0, r.stdout + r.stderr
    [entry] = _stored(store, "plural_hedge_seed.json")
    assert entry["count"] == 1 and "GOH.SEEDSTALE" in entry["reason"], entry
    assert "lowered to 1 hedged plural" in r.stderr, r.stderr


def test_a_deleted_seeded_file_deletes_its_store_entry(repo: Path, tmp_path: Path) -> None:
    store = _seeded(repo, tmp_path, "plural-hedge", "plural_hedge_seed.json", "a.py", ONE)
    git(repo, "rm", "-q", "a.py")
    r = _run(repo, store, "plural-hedge", "--staged")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _stored(store, "plural_hedge_seed.json") == [], r.stderr


def test_another_repos_store_entry_is_untouched(repo: Path, tmp_path: Path) -> None:
    store = _seeded(repo, tmp_path, "plural-hedge", "plural_hedge_seed.json", "a.py", ONE)
    data = json.loads((store / "plural_hedge_seed.json").read_text())
    other = {**data["entries"][0], "root": "0" * 40, "repo": "other"}
    data["entries"].append(other)
    (store / "plural_hedge_seed.json").write_text(json.dumps(data))
    write(repo, "a.py", "print(n)\n")
    stage(repo, "a.py")
    r = _run(repo, store, "plural-hedge", "--staged")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _stored(store, "plural_hedge_seed.json") == [other], "only this repository's entry"


def test_a_tracked_entry_the_commit_leaves_stale_is_red_at_the_commit(
    repo: Path, tmp_path: Path
) -> None:
    write(repo, "a.py", ONE)
    commit_all(repo)
    write(repo, "plural_hedge_seed.json", _seed(_root(repo), "a.py", 1))
    commit_all(repo)
    store = tmp_path / "no-store"
    write(repo, "a.py", "print(n)\n")
    stage(repo, "a.py")
    r = _run(repo, store, "plural-hedge", "--staged")
    assert r.returncode == 1, "the commit can delete a tracked entry, so it must: " + r.stderr
    assert "delete its entry from plural_hedge_seed.json in this commit" in r.stderr, r.stderr
    write(repo, "plural_hedge_seed.json", json.dumps({"why": "x", "entries": []}))
    stage(repo, "plural_hedge_seed.json")
    assert _run(repo, store, "plural-hedge", "--staged").returncode == 0


def test_the_push_still_calls_a_store_entry_stale_when_no_commit_settled_it(
    repo: Path, tmp_path: Path
) -> None:
    """The backstop: a fix committed with no commit-time run (an older goh, `--no-verify`)."""
    store = _seeded(repo, tmp_path, "plural-hedge", "plural_hedge_seed.json", "a.py", ONE)
    write(repo, "a.py", "print(n)\n")
    commit_all(repo, "fix with no commit-time run")
    push = _run(repo, store, "plural-hedge")
    assert push.returncode == 1 and "stale" in push.stderr, push.stdout + push.stderr


def test_a_changelog_fixed_at_the_commit_settles_its_store_entry(
    repo: Path, tmp_path: Path
) -> None:
    """changelog_tags reads the whole changelog at both scopes: a fix at the commit, once red."""
    write(repo, "CHANGELOG.md", "# Changelog\n")
    commit_all(repo)
    git(repo, "tag", "v0.1.0")
    store = tmp_path / "store"
    store.mkdir()
    (store / "changelog_tags_seed.json").write_text(_seed(_root(repo), "CHANGELOG.md", 1))
    assert _run(repo, store, "changelog-tags").returncode == 0, (
        "the tag's missing heading is seeded"
    )
    write(repo, "CHANGELOG.md", "# Changelog\n\n## v0.1.0\n")
    stage(repo, "CHANGELOG.md")
    r = _run(repo, store, "changelog-tags", "--staged")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _stored(store, "changelog_tags_seed.json") == [], r.stderr
