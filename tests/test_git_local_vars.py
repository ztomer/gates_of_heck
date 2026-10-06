"""git's repository-binding variables are asked of git ONCE per process tree (2026-10-06).

`git rev-parse --local-env-vars` names the variables a git call on a FOREIGN repository must drop
(GIT_DIR, GIT_INDEX_FILE, ...: contract #12). Its answer is fixed for a git binary, and the
gates asked for it about 1300 times per suite run -- three times per `goh.sh` call alone.
`gates/_from_head.sh`, sourced first by every gate, asks once and exports GOH_GIT_LOCAL_VARS;
every site and `checks/_gitutil.py` read it. Because that list is what keeps GIT_DIR out of a
foreign repository, an inherited value is trusted only when it names GIT_DIR: anything else is
asked again.
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

GITS = subprocess.run(
    ["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=True
).stdout.split()
REAL_GIT = subprocess.run(
    ["sh", "-c", "command -v git"], capture_output=True, text=True
).stdout.strip()


def _sourced(env: dict) -> list[str]:
    r = subprocess.run(
        ["bash", "-c", f'. "{REPO_ROOT}/gates/_from_head.sh"; printf %s "$GOH_GIT_LOCAL_VARS"'],
        capture_output=True,
        text=True,
        env=env,
    )
    return r.stdout.split()


def test_the_list_is_gits_own() -> None:
    assert _sourced(hermetic_env()) == GITS


def test_an_inherited_list_without_git_dir_is_asked_again() -> None:
    assert _sourced(hermetic_env(GOH_GIT_LOCAL_VARS="NOT_A_GIT_VAR")) == GITS


def test_python_takes_a_valid_inherited_list_and_refuses_an_invalid_one() -> None:
    probe = (
        f"import sys; sys.path.insert(0, {str(REPO_ROOT / 'checks')!r}); "
        "import _gitutil; print(' '.join(_gitutil.local_env_vars()))"
    )
    run = lambda value: subprocess.run(  # noqa: E731
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_GIT_LOCAL_VARS=value),
    ).stdout.split()
    assert run("GIT_DIR GIT_SENTINEL") == ["GIT_DIR", "GIT_SENTINEL"]  # taken, not re-asked
    assert run("NOT_A_GIT_VAR") == GITS


def test_a_goh_sh_call_asks_git_for_the_list_at_most_once(goh: Path, tmp_path: Path) -> None:
    shim, log = tmp_path / "shim", tmp_path / "git.log"
    shim.mkdir()
    (shim / "git").write_text(f'#!/bin/sh\necho "$*" >> "{log}"\nexec "{REAL_GIT}" "$@"\n')
    (shim / "git").chmod(0o755)
    env = hermetic_env()
    env["PATH"] = f"{shim}:{env['PATH']}"
    subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "goh.sh"), "emoji"],
        cwd=tmp_path,
        capture_output=True,
        env=env,
    )
    calls = Counter(log.read_text().splitlines()) if log.exists() else Counter()
    assert calls["rev-parse --local-env-vars"] <= 1, calls
