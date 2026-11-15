"""`goh tui-marks` and `goh plural-hedge --all` end to end (GOH.TUIMARKS, GOH.HEDGELIST): the CLI
tier beside `tuimarks/tests.rs` and `pluralhedge/tests.rs`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from _fast_git import fast_init
from conftest import hermetic_env

# The marks are assembled (`CROSS`): goh's own tree is read by the finder this file tests.
CROSS = "✗"
PLANTED = f'#!/usr/bin/env bash\necho "{CROSS} adb not found"\n'
HOUSE = '#!/usr/bin/env bash\n. "$GOH_DIR/tui/lib.sh"\nerr "adb not found"\n'


def _git(repo: Path, *args: str) -> str:
    env = hermetic_env(drop_git=True)
    return subprocess.run(["git", "-C", str(repo), *args], check=True, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, env=env).stdout  # fmt: skip


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "r"
    fast_init(repo)
    for rel, text in files.items():
        (repo / rel).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x", "--no-verify")
    return repo


def _run(
    goh: Path, repo: Path, store: Path, *args: str, grace: str = "over"
) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(drop_git=True, GOH_SEED_DIR=str(store), GOH_GRACE=grace)
    return subprocess.run([str(goh), *args], cwd=repo, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, env=env, check=False)  # fmt: skip


def test_a_planted_mark_is_red_a_seeded_one_warns_and_the_grace_judges_nothing(
    goh: Path, tmp_path: Path
) -> None:
    repo = _repo(tmp_path, {"setup.sh": PLANTED, "clean.sh": HOUSE})
    store = tmp_path / "store"
    store.mkdir()
    red = _run(goh, repo, store, "tui-marks")
    assert red.returncode == 1, red.stdout + red.stderr
    assert "setup.sh:2:" in red.stderr and "clean.sh" not in red.stderr, red.stderr
    within = _run(goh, repo, store, "tui-marks", grace="within")
    assert within.returncode == 0, within.stdout + within.stderr

    root = _git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    entry = {"root": root, "repo": "r", "path": "setup.sh", "count": 1, "reason": "unreviewed"}
    (store / "tui_marks_seed.json").write_text(json.dumps({"entries": [entry]}))
    seeded = _run(goh, repo, store, "tui-marks")
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    assert "seeded" in seeded.stderr and "setup.sh" in seeded.stderr, seeded.stderr


def test_plural_hedge_all_lists_every_site_of_a_seeded_file(goh: Path, tmp_path: Path) -> None:
    hedge = "file" + "(s)"
    body = "".join(f'echo "{i} {hedge}"\n' for i in range(1, 13))
    repo = _repo(tmp_path, {"a.sh": body})
    store = tmp_path / "store"
    store.mkdir()
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    entry = {"root": root, "repo": "r", "path": "a.sh", "count": 12, "reason": "unreviewed"}
    (store / "plural_hedge_seed.json").write_text(json.dumps({"entries": [entry]}))
    short = _run(goh, repo, store, "plural-hedge")
    assert short.returncode == 0 and "a.sh:6:" not in short.stderr, short.stderr
    assert "... and 7 more, counted in the seed (--all lists them)" in short.stderr, short.stderr
    full = _run(goh, repo, store, "plural-hedge", "--all")
    assert full.returncode == 0, full.stdout + full.stderr
    assert all(f"a.sh:{i}:" in full.stderr for i in range(1, 13)), full.stderr
    assert "more, counted" not in full.stderr, full.stderr
