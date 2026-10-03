"""The one line every `check_no_unreaped_spawn.py` test file starts from.

A helper written three times is a helper that will be written three different ways, which is the
estate's most repeated finding about itself. One definition, imported by name, so a change to how
the checker is invoked cannot land in two of the three files.
"""

from pathlib import Path
import subprocess

from conftest import run_check

CHECK = "checks/check_no_unreaped_spawn.py"


def findings(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the checker over `repo` (cwd=repo), exactly as a consumer's gate does."""
    return run_check(repo, CHECK, *args)
