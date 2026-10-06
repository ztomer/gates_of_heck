"""The one line every `check_no_unreaped_spawn.py` test file starts from -- for BOTH tiers.

A helper written three times is a helper that will be written three different ways, which is the
estate's most repeated finding about itself. One definition, imported by name, so a change to how
the checker is invoked cannot land in two of the three files.

Every test that calls `findings` runs twice: against the Python checker and against its native port
(`goh unreaped-spawn`, Phase N1), whose report is the reference's line for line. A module opts in
with `pytestmark = pytest.mark.usefixtures("unreaped_tier")` after importing `unreaped_tier`. The
two Python-only flags (`--probe`, `--fresh-derivations`) skip on the native tier: the probe's table
is run through the port by `tests/test_unreaped_spawn_native_parity.py`, and the port has no memo.
"""

from pathlib import Path
import subprocess

import pytest

from conftest import run_check

CHECK = "checks/check_no_unreaped_spawn.py"
PYTHON_ONLY = ("--probe", "--fresh-derivations")

_TIER: dict[str, object] = {"name": "python", "goh": None}


@pytest.fixture(params=["python", "native"])
def unreaped_tier(request, goh: Path):
    _TIER.update(name=request.param, goh=goh)
    yield request.param
    _TIER.update(name="python", goh=None)


def findings(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the checker over `repo` (cwd=repo), exactly as a consumer's gate does."""
    if _TIER["name"] == "python":
        return run_check(repo, CHECK, *args)
    if any(flag in args for flag in PYTHON_ONLY):
        pytest.skip("a Python-only flag; the native tier is pinned by the parity table")
    return subprocess.run(
        [str(_TIER["goh"]), "unreaped-spawn", *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )
