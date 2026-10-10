"""`goh fixed-sleep` end to end in a scratch repo: red on a new site, seeded, green, then red
again when a fixed site leaves the seed too high -- the CLI tier beside the unit table in
`crates/goh/src/fixedsleep/tests.rs`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from _fast_git import fast_init
from conftest import hermetic_env

STAND_IN = "import time\np = start()\ntime.sleep(1)\nif p.poll() is None:\n    held = True\n"
POLLED = "import time\nwhile p.poll() is None:\n    time.sleep(0.1)\n"


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "r"
    fast_init(repo)
    for rel, text in files.items():
        (repo / rel).write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, stdin=subprocess.DEVNULL,
                   capture_output=True, env=hermetic_env(drop_git=True))  # fmt: skip
    return repo


def _run(goh: Path, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(goh), "fixed-sleep", *args], cwd=repo, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, env=hermetic_env(drop_git=True), check=False)  # fmt: skip


def test_a_site_is_red_until_seeded_and_a_fix_must_lower_the_seed(
    goh: Path, tmp_path: Path
) -> None:
    repo = _repo(tmp_path, {"a.py": STAND_IN, "b.py": POLLED})
    red = _run(goh, repo)
    assert red.returncode == 1 and "a.py:3:" in red.stdout and "b.py" not in red.stdout, red.stdout

    seeded = _run(goh, repo, "--seed")
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    assert json.loads((repo / "fixed_sleep_seed.json").read_text())["files"] == {"a.py": 1}
    green = _run(goh, repo)
    assert green.returncode == 0 and "2 Python file(s) read, 1 seeded" in green.stdout, green.stdout

    (repo / "a.py").write_text(POLLED)
    stale = _run(goh, repo)
    assert stale.returncode == 1 and "lower the seed" in stale.stdout, stale.stdout


def test_a_corrupt_seed_is_an_error_not_an_empty_one(goh: Path, tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"a.py": STAND_IN, "fixed_sleep_seed.json": "{"})
    r = _run(goh, repo)
    assert r.returncode == 2 and "not JSON" in r.stderr, r.stdout + r.stderr
