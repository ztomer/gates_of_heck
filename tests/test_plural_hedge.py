"""`goh plural-hedge` end to end (GOH.PLURALHEDGE): a planted `"{n} step(s) ran"` is red, a counted
message and a comment are clean, and a site seeded in the estate seed (keyed by the repo's root
commit, read from the judged repo's own file or the private store, never `$GOH_DIR`) warns instead -- the CLI tier beside `pluralhedge/tests.rs`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from _fast_git import fast_init
from conftest import hermetic_env

PLANTED = 'print(f"{n} step(s) ran")\n'
HOUSE = "from tui.lib import counted\nprint(counted(n, 'step'))  # never step(s)\n"
SHELL = '#!/usr/bin/env bash\necho "$n entr(ies) open"\n'


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, env=hermetic_env(drop_git=True)).stdout  # fmt: skip


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "r"
    fast_init(repo)
    for rel, text in files.items():
        (repo / rel).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x", "--no-verify")
    return repo


def _run(goh: Path, repo: Path, store: Path) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(drop_git=True, GOH_SEED_DIR=str(store))
    return subprocess.run([str(goh), "plural-hedge"], cwd=repo, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, env=env, check=False)  # fmt: skip


def test_a_planted_hedge_is_red_and_a_seeded_one_warns(goh: Path, tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"a.py": PLANTED, "b.py": HOUSE, "run": SHELL})
    seedless = tmp_path / "store"
    seedless.mkdir()
    red = _run(goh, repo, seedless)
    assert red.returncode == 1, red.stdout + red.stderr
    assert "a.py:1:" in red.stderr and "run:2:" in red.stderr, red.stderr
    assert "b.py" not in red.stderr and "2 hedged plurals" in red.stderr, red.stderr

    root = _git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    entries = [{"root": root, "repo": "r", "path": p, "count": 1, "reason": "unreviewed"}
               for p in ("a.py", "run")]  # fmt: skip
    (seedless / "plural_hedge_seed.json").write_text(json.dumps({"entries": entries}))
    seeded = _run(goh, repo, seedless)
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    assert "seeded" in seeded.stderr and "a.py" in seeded.stderr, seeded.stderr
    # The same entries in the repo's own tracked file, the home of a repo gated on a runner.
    (seedless / "plural_hedge_seed.json").rename(repo / "plural_hedge_seed.json")
    _git(repo, "add", "plural_hedge_seed.json")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed", "--no-verify")
    tracked = _run(goh, repo, seedless)
    assert tracked.returncode == 0, tracked.stdout + tracked.stderr
    assert "seeded" in tracked.stderr and "run" in tracked.stderr, tracked.stderr


def test_a_clean_repo_passes(goh: Path, tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"b.py": HOUSE})
    ok = _run(goh, repo, tmp_path)
    assert ok.returncode == 0 and "every count in a string meets its noun" in ok.stdout, ok.stdout
