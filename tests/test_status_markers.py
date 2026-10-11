"""`goh status-markers` end to end in a scratch repo: the planted `print("[ Ok  ] done")` is red,
seeded, green, then red again when a fixed site leaves the seed too high; a doc quoting the style
is never read -- the CLI tier beside the unit table in `crates/goh/src/statusmark/tests.rs`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from _fast_git import fast_init
from conftest import hermetic_env

PLANTED = 'print("[ Ok  ] done")\n'
HOUSE = "from tui.lib import ok\nok('done')\n"
DOC = 'Never print `"[ Ok  ]"` or `"\\033[32m"` by hand.\n'


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "r"
    fast_init(repo)
    for rel, text in files.items():
        (repo / rel).write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, stdin=subprocess.DEVNULL,
                   capture_output=True, env=hermetic_env(drop_git=True))  # fmt: skip
    return repo


def _run(goh: Path, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(goh), "status-markers", *args], cwd=repo, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, env=hermetic_env(drop_git=True), check=False)  # fmt: skip


def test_a_planted_marker_is_red_until_seeded_and_a_fix_must_lower_the_seed(
    goh: Path, tmp_path: Path
) -> None:
    repo = _repo(tmp_path, {"a.py": PLANTED, "b.py": HOUSE, "README.md": DOC})
    red = _run(goh, repo)
    assert red.returncode == 1 and "a.py:1:" in red.stdout, red.stdout
    assert "b.py" not in red.stdout and "README" not in red.stdout, red.stdout

    seeded = _run(goh, repo, "--seed")
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    assert json.loads((repo / "status_marker_seed.json").read_text())["files"] == {"a.py": 1}
    green = _run(goh, repo)
    assert green.returncode == 0 and "2 source file(s) read, 1 seeded" in green.stdout, green.stdout

    (repo / "a.py").write_text(HOUSE)
    stale = _run(goh, repo)
    assert stale.returncode == 1 and "goh status-markers --seed" in stale.stdout, stale.stdout


def test_a_corrupt_seed_is_an_error_not_an_empty_one(goh: Path, tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"a.py": PLANTED, "status_marker_seed.json": "{"})
    r = _run(goh, repo)
    assert r.returncode == 2 and "not JSON" in r.stderr, r.stdout + r.stderr
