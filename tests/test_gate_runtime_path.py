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
    (gates / "check_crashes.py").write_text("from tui import no_such_part\n")
    (gates / "check_refuses.py").write_text("import sys\nprint('nothing to judge')\nsys.exit(1)\n")
    # app_updates' check_literal_list.py, 2026-10-05: it reads its subject unconditionally and dies
    # on an empty tree. That is a REFUSAL -- loud, non-zero, never a false pass -- and it went red
    # in two consumers when every Traceback counted as "the sweep could not run it".
    (gates / "check_reads_its_subject.py").write_text("open('crates/core/src/lib.rs').read()\n")
    blind, unrunnable = check_empty_scope.sweep(
        str(tmp_path),
        "checks",
        ["check_crashes.py", "check_refuses.py", "check_reads_its_subject.py"],
    )
    assert (
        "check_reads_its_subject.py" not in unrunnable and "check_reads_its_subject.py" not in blind
    )
    assert "check_crashes.py" in unrunnable and "crashed" in unrunnable["check_crashes.py"], (
        blind,
        unrunnable,
    )
    assert "check_refuses.py" not in unrunnable and "check_refuses.py" not in blind


def test_an_import_of_the_consumers_own_package_is_unmeasurable_not_a_failure(tmp_path):
    """divoom-control, ZeroThunder and game_asset_factory each have a checker importing the repo's
    OWN package, which the skeleton holds none of. That is the sweep's limit; an import of THIS
    checkout's runtime (tui, lib/) failing is the sweep's defect and stays a failure."""
    gates = tmp_path / "checks"
    gates.mkdir()
    (gates / "check_own_package.py").write_text("import divoom_client_fake.binary_resolver\n")
    (gates / "check_runtime.py").write_text("import tui.no_such_part\n")
    _blind, unrunnable = check_empty_scope.sweep(
        str(tmp_path), "checks", ["check_own_package.py", "check_runtime.py"]
    )
    assert unrunnable["check_own_package.py"].startswith(check_empty_scope.UNMEASURABLE), unrunnable
    assert unrunnable["check_runtime.py"].startswith("crashed"), unrunnable
