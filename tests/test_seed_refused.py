"""A commit refused by ANY step of its gate settles no seed entry (GOH.SEEDREFUSED, 2026-11-12).

GOH.SEEDSTALE (D-0005) let a commit's own run of a seeded check lower or delete the private-store
entries its change settles. The check wrote the store the moment IT passed, so a commit refused by
a LATER step of the same gate -- the repository's tests, the line cap, the python twin -- had
already pruned the store: on 2026-11-12 a necrohand agent's refused commits deleted 13 plural_hedge
entries for files it then reverted, and the entries had to be restored by hand. Now the hook
(gates/commit_gate.sh) hands every check a pending directory (GOH_SEED_PENDING), a check journals
its settle there instead of writing the store, and the hook applies the journal only once every
step has passed. Every test uses a scratch store, never the machine's.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env, native_goh_path

# A hedge assembled from pieces, which goh's own plural_hedge step does not read as one.
S = "(" + "s)"
SEEDED = f'n = 1\nprint(f"{{n}} step{S} ran")\n'
FIXED = "n = 1\nprint(n)\n"

# The repository's gate: the seeded check settles (and passes), the house structural layer runs,
# then a LATER step decides. The python twin is marked as run, so ruff is not this test's subject.
GATE = """#!/usr/bin/env bash
set -euo pipefail
"$GOH_BIN" plural-hedge --staged
printf 'py_staged\\t%s\\n' "$(pwd -P)" >> "$GOH_STAGED_TWIN_MARK"
bash "$GOH_DIR/gates/structural.sh" --staged
exit "${LATER_STEP:-0}"
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()


def _consumer(tmp_path: Path) -> tuple[Path, Path]:
    """A repository with the stock hook and GATE, `a.py` seeded in a scratch private store."""
    repo = tmp_path / "repo"
    (repo / ".githooks").mkdir(parents=True)
    hook = repo / ".githooks" / "pre-commit"
    hook.write_bytes((REPO_ROOT / "hooks" / "pre-commit").read_bytes())
    hook.chmod(0o755)
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text(GATE)
    (repo / "a.py").write_text(SEEDED)
    (repo / "b.py").write_text(SEEDED)
    fast_init(repo, "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "one")
    _git(repo, "config", "core.hooksPath", ".githooks")
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    store = tmp_path / "store"
    store.mkdir()
    entries = [
        {"root": root, "repo": "fixture", "path": p, "count": 1, "reason": "a test's"}
        for p in ("a.py", "b.py")
    ]
    (store / "plural_hedge_seed.json").write_text(
        json.dumps({"why": "a test's", "entries": entries}, indent=1) + "\n"
    )
    return repo, store


def _commit(repo: Path, store: Path, later: int) -> subprocess.CompletedProcess:
    (repo / "a.py").write_text(FIXED)
    _git(repo, "add", "a.py")
    env = hermetic_env(
        GOH_DIR=str(REPO_ROOT),
        GOH_BIN=str(native_goh_path()),
        GOH_SEED_DIR=str(store),
        LATER_STEP=str(later),
    )
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "fix a.py"],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _snapshot(store: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(store.iterdir())}


def test_a_commit_refused_by_a_later_step_leaves_every_seed_store_byte_identical(
    tmp_path: Path,
) -> None:
    repo, store = _consumer(tmp_path)
    before = _snapshot(store)
    got = _commit(repo, store, later=1)
    assert got.returncode != 0, "the later step refuses the commit: " + got.stdout + got.stderr
    assert _snapshot(store) == before, (
        "a refused commit wrote the seed store (GOH.SEEDREFUSED): " + got.stderr
    )
    assert _git(repo, "rev-list", "--count", "HEAD") == "1", "nothing was committed"


def test_a_passing_commit_still_settles_its_store_entry(tmp_path: Path) -> None:
    repo, store = _consumer(tmp_path)
    got = _commit(repo, store, later=0)
    assert got.returncode == 0, got.stdout + got.stderr
    entries = json.loads((store / "plural_hedge_seed.json").read_text())["entries"]
    assert [e["path"] for e in entries] == ["b.py"], (
        "the passing commit deleted a.py's settled entry, and only it (D-0005): " + got.stderr
    )
    assert "GOH.SEEDSTALE" in got.stderr and "entry deleted" in got.stderr, got.stderr
    assert not list(store.glob(".*.tmp")), "no temp file is left in the store"
