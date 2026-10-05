"""A gate's Python finds its own runtime (tui.lib) without help from the caller's shell, and the
empty-tree sweep never reads a checker that CRASHED as one that refused.

2026-10-05: `~/.zshrc` exports `PYTHONPATH=$GOH_DIR`, and non-interactive shells never read
`~/.zshrc`. ZoneWM's tools failed `from tui.lib import ...` there and its Makefile now exports the
path itself; in a push export the empty-tree sweep's skeleton mirrors `tui/` EMPTY, so with no
PYTHONPATH every checker died on `ModuleNotFoundError` -- and the sweep scored each crash (exit 1)
as a healthy refusal of the empty tree.
"""

import os
import subprocess
import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "checks"))
import check_empty_scope  # noqa: E402


def test_every_gate_exports_its_own_runtime_path():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    got = subprocess.run(
        ["bash", "-c", f'. "{REPO_ROOT}/gates/_common.sh"; printf %s "$PYTHONPATH"'],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert str(REPO_ROOT) in got.stdout.split(":"), got.stdout


def test_a_crashing_checker_is_unrunnable_not_a_refusal(tmp_path):
    gates = tmp_path / "checks"
    gates.mkdir()
    (gates / "check_crashes.py").write_text("import no_such_module_anywhere\n")
    (gates / "check_refuses.py").write_text("import sys\nprint('nothing to judge')\nsys.exit(1)\n")
    blind, unrunnable = check_empty_scope.sweep(
        str(tmp_path), "checks", ["check_crashes.py", "check_refuses.py"]
    )
    assert "check_crashes.py" in unrunnable and "crashed" in unrunnable["check_crashes.py"], (
        blind,
        unrunnable,
    )
    assert "check_refuses.py" not in unrunnable and "check_refuses.py" not in blind
