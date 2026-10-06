"""The one line every `check_no_unreaped_spawn.py` test file starts from -- for BOTH tiers.

A helper written three times is a helper that will be written three different ways, which is the
estate's most repeated finding about itself. One definition, imported by name, so a change to how
the checker is invoked cannot land in two of the three files. The tier machinery is `tier_kit`'s;
the parity of the measured table is `tests/test_unreaped_spawn_native_parity.py`.
"""

from pathlib import Path
import subprocess

from tier_kit import both_tiers as unreaped_tier  # noqa: F401  # re-exported fixture
from tier_kit import run_tiered

CHECK = "checks/check_no_unreaped_spawn.py"


def findings(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the checker over `repo` (cwd=repo), exactly as a consumer's gate does."""
    return run_tiered(
        repo, CHECK, "unreaped-spawn", *args, python_only=("--probe", "--fresh-derivations")
    )
