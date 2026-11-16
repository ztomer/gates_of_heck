"""A STALE store entry for a repository whose CI runs goh retires itself (GOH.STORECISTALE).

GOH.STORECI refuses a private-store entry for a repository whose CI runs goh: a runner cannot read
the store. It refused at LOAD, before any run had judged the entry, so in token-pool (2026-11-16)
it fired on entries whose sites were already fixed, saying only "move it into the tracked seed";
the lane hand-deleted them from the store. Now the refusal is judged after the findings:

* a full run that finds the entry's site clean RETIRES it from the store (the settle seam
  GOH.SEEDSTALE and GOH.SEEDPRUNE write through) and says so, green;
* an entry whose site is still there is refused as before, naming the repository and its workflow;
* a commit's run, which reads only what it changes, cannot tell, so it refuses naming the full run
  (`goh structural --full`) that retires an entry whose site is fixed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import commit_all, git, hermetic_env, run_goh, stage, write

QUIET = "#!/usr/bin/env bash\nset -euo pipefail\ncargo test >/dev/null\n"
CLEAN = "#!/usr/bin/env bash\nset -euo pipefail\ncargo test\n"
FILE = "release_quiet_seed.json"
CI = (
    "jobs:\n  gate:\n    runs-on: ubuntu-24.04\n    steps:\n"
    "      - uses: actions/checkout@v5\n"
    "        with:\n          repository: ztomer/gates_of_heck\n"
)
ELSEWHERE = "0" * 40


def _store(repo: Path, store: Path) -> str:
    root = git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    store.mkdir()
    entries = [
        {"root": root, "repo": "fixture", "path": "release.sh", "count": 1, "reason": "unreviewed"},
        {"root": ELSEWHERE, "repo": "other", "path": "release.sh", "count": 1, "reason": "kept"},
    ]
    (store / FILE).write_text(json.dumps({"why": "a test's", "entries": entries}))
    return root


def _ci_repo(repo: Path, script: str) -> None:
    write(repo, ".github/workflows/ci.yml", CI)
    write(repo, "release.sh", script)
    commit_all(repo)


def _quiet(repo: Path, store: Path, *args: str) -> subprocess.CompletedProcess:
    return run_goh(repo, "release-quiet", *args, env=hermetic_env(GOH_SEED_DIR=str(store)))


def _roots(store: Path) -> list[str]:
    return [e["root"] for e in json.loads((store / FILE).read_text())["entries"]]


def test_a_full_run_retires_a_stale_store_entry_of_a_ci_repo(repo: Path, tmp_path: Path) -> None:
    """The token-pool case: the site is fixed, the store entry is left over."""
    _ci_repo(repo, CLEAN)
    store = tmp_path / "store"
    _store(repo, store)
    r = _quiet(repo, store)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "release.sh" in r.stderr and "retired" in r.stderr, r.stderr
    assert "GOH.STORECI" in r.stderr, r.stderr
    assert _roots(store) == [ELSEWHERE], "only this repository's stale entry goes"
    again = _quiet(repo, store)
    assert again.returncode == 0 and "retired" not in again.stderr, again.stderr


def test_a_live_store_entry_of_a_ci_repo_is_still_refused(repo: Path, tmp_path: Path) -> None:
    _ci_repo(repo, QUIET)
    store = tmp_path / "store"
    root = _store(repo, store)
    r = _quiet(repo, store)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "runs goh in GitHub CI" in r.stderr and "ci.yml" in r.stderr, r.stderr
    assert "GOH.STORECI" in r.stderr and "retired" not in r.stderr, r.stderr
    assert _roots(store) == [root, ELSEWHERE], "a live entry is never retired"


def test_a_commit_run_names_the_full_run_that_retires_a_fixed_site(
    repo: Path, tmp_path: Path
) -> None:
    _ci_repo(repo, CLEAN)
    store = tmp_path / "store"
    root = _store(repo, store)
    write(repo, "README.md", "# changed\n")
    stage(repo, "README.md")
    r = _quiet(repo, store, "--staged")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "GOH.STORECI" in r.stderr and "goh structural --full" in r.stderr, r.stderr
    assert _roots(store) == [root, ELSEWHERE], "a commit's run retires nothing it did not read"
