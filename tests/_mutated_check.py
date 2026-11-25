"""`mutated_probe(name, source)`: the display-seam probe run with one module of checks/ replaced by
SOURCE, in a process of its own and from memory: no copy of checks/ is written (GOH.FSEVQUIET)."""

from __future__ import annotations

import subprocess
import sys

from conftest import REPO_ROOT
from timing_bounds import patience

# The module under its own name and path, before anything imports it; then the gate's self-proof.
_RUN = """\
import sys, types
checks, name = sys.argv[1:]
sys.path[:0] = [checks]
sys.dont_write_bytecode = True
module = sys.modules[name] = types.ModuleType(name)
module.__file__ = f"{checks}/{name}.py"
exec(compile(sys.stdin.read(), module.__file__, "exec"), vars(module))
import check_display_seam
sys.exit(check_display_seam.main(["--probe"]))
"""


def mutated_probe(name: str, source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _RUN, str(REPO_ROOT / "checks"), name],
        input=source, capture_output=True, text=True, check=False, cwd="/", timeout=patience(60),
    )  # fmt: skip
