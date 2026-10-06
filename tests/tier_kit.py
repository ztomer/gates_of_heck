"""Run a checker's whole-repo tests against the native port (Phase N3: the only tier).

These suites ran twice -- the Python checker and its port -- until the Python was retired. They
keep the old call shape so each test still reads as the behaviour it pins:

    from tier_kit import both_tiers  # noqa: F401  (a fixture)
    pytestmark = pytest.mark.usefixtures("both_tiers")

and `run_tiered(repo, script, subcommand, *args)` runs `goh <subcommand> args`. The Python's own
behaviour is the frozen spec in `tests/reference_kit.py`, compared by the parity suites.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_TIER: dict[str, object] = {"goh": None}


@pytest.fixture
def both_tiers(goh: Path):
    _TIER.update(goh=goh)
    yield "native"
    _TIER.update(goh=None)


def run_tiered(
    repo: Path,
    script: str,
    subcommand: str,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """`goh <subcommand> args`, cwd=repo. `script` names the retired checker the test was written
    against, kept so the call site still says which behaviour it pins."""
    del script
    if _TIER["goh"] is None:
        raise RuntimeError("run_tiered needs the both_tiers fixture (pytestmark usefixtures)")
    argv = [str(_TIER["goh"]), subcommand, *args]
    return subprocess.run(argv, cwd=repo, capture_output=True, text=True, env=env)
