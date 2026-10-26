"""goh's suite never writes the user's push caches (G11, 2026-10-26).

THE CLASS. A test ran gates/push_gate.sh with its GOH_* scrubbed, so the gate fell back to the
REAL ~/.cache/goh/push-logs and left a zero-byte `repo-<sha>-<time>.log` there at every suite run.
Two halves close it: every test gets private push roots by default (tests/host_locks.py, kept by
every env scrub through `scrubbed`), and the push gate refuses to run under this suite without them
(lib/push_runs.sh) -- so a new test that forgets is red, not a quiet write to the user's cache.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from _fast_git import fast_init
from host_locks import CACHE_ROOTS, scrubbed

REPO_ROOT = Path(__file__).resolve().parent.parent
PUSH_GATE = REPO_ROOT / "gates" / "push_gate.sh"


def test_every_test_gets_private_push_roots() -> None:
    home_cache = os.path.realpath(os.path.expanduser("~/.cache/goh"))
    for name in CACHE_ROOTS:
        value = os.environ.get(name, "")
        assert value, f"{name} is not set for this test"
        assert not os.path.realpath(value).startswith(home_cache + "/push"), (name, value)


def test_an_env_scrub_keeps_the_private_roots() -> None:
    env = scrubbed(os.environ, ("GOH_", "GIT_"))
    for name in CACHE_ROOTS:
        assert env.get(name) == os.environ[name], name


def test_the_push_gate_refuses_the_users_cache_under_this_suite(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    fast_init(repo, "main")
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    env = {k: v for k, v in os.environ.items() if k not in CACHE_ROOTS}
    env["GOH_DIR"] = str(REPO_ROOT)
    proc = subprocess.run(
        ["bash", str(PUSH_GATE)],
        cwd=repo,
        env=env,
        input=f"refs/heads/main {'1' * 40} refs/heads/main {'0' * 40}\n",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "GOH_PUSH_LOGS" in proc.stderr and "test_the_push_gate_refuses" in proc.stderr, (
        proc.stderr
    )
