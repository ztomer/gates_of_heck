"""Run a checker's whole-repo tests against BOTH tiers: the Python checker and its native port.

Phase N ports each delegated checker to `bin/goh` and the Python stays the spec until N2/N3. A
test that only runs the Python proves nothing about the port, so a module opts in with

    from tier_kit import both_tiers  # noqa: F401  (a fixture)
    pytestmark = pytest.mark.usefixtures("both_tiers")

and calls `run_tiered(repo, script, subcommand, *args)` where it called `run_check`. Every such test
then runs twice. A flag only the Python has (`--probe` and the like) skips on the native tier; the
port's agreement with the checker's measured table is a parity test of its own.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT, run_check

_TIER: dict[str, object] = {"name": "python", "goh": None}


@pytest.fixture(params=["python", "native"])
def both_tiers(request, goh: Path):
    _TIER.update(name=request.param, goh=goh)
    yield request.param
    _TIER.update(name="python", goh=None)


def run_tiered(
    repo: Path,
    script: str,
    subcommand: str,
    *args: str,
    python_only: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """`python3 <script> args` or `goh <subcommand> args`, cwd=repo, by the current tier."""
    if _TIER["name"] == "python":
        if env is None:
            return run_check(repo, script, *args)
        argv = ["python3", str(REPO_ROOT / script), *args]
    else:
        if any(flag in args for flag in python_only):
            pytest.skip("a Python-only flag; the port is pinned by its parity test")
        argv = [str(_TIER["goh"]), subcommand, *args]
    return subprocess.run(argv, cwd=repo, capture_output=True, text=True, env=env)
