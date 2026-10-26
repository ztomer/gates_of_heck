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
        stdin=subprocess.DEVNULL,
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


def _sweep_repo(tmp_path, excuses: dict) -> "subprocess.CompletedProcess":
    import json

    repo = tmp_path / "repo"
    gates = repo / "checks"
    gates.mkdir(parents=True)
    (gates / "check_refuses.py").write_text("import sys\nprint('nothing to judge')\nsys.exit(1)\n")
    (gates / "check_no_emoji.py").write_text(
        "import os, sys\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
        '__import__("_retired").forward("emoji", __name__)\n'
    )
    (gates / "empty_scope_allow.json").write_text(json.dumps({"legitimate": excuses}))
    subprocess.run(["git", "init", "-q", str(repo)], check=True, stdin=subprocess.DEVNULL)
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "checks" / "check_empty_scope.py"), "--root", str(repo)],
        capture_output=True,
        text=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def test_a_forwarder_is_named_and_not_swept(tmp_path):
    """A retired checker's entry point forwards to a native check (Phase N3). Swept as a Python
    gate, it fails in the skeleton for want of its dispatcher -- a verdict about the sweep, not
    about the check -- so it is named and skipped."""
    got = _sweep_repo(tmp_path, {})
    assert got.returncode == 0, got.stdout + got.stderr
    assert "check_no_emoji.py forwards to a native check" in got.stdout, got.stdout
    assert "1 gate(s) swept" in got.stdout, got.stdout


def test_an_excuse_naming_no_gate_is_a_finding(tmp_path):
    """Stale permission: an excuse for a file that is gone would excuse the next file to take the
    name. Seven such excuses outlived the checkers Phase N3 deleted."""
    got = _sweep_repo(tmp_path, {"check_gone.py": "it used to be here"})
    assert got.returncode == 1, got.stdout + got.stderr
    assert "check_gone.py is excused here but names no gate" in got.stderr, got.stderr


# THE SHELL HALF OF THE CLASS (Adv360-Pro-ZMK, 2026-10-20). The sweep collected `check_*.py` and
# nothing else, so a repo's `check_*.sh` gates -- Adv360's `check_keymap_build.sh` and
# `check_upstream_drift.sh`, both with probes -- were never swept: a shell gate could report
# success over a tree with no content and nothing in the estate would ever say so.


def test_a_shell_gate_is_swept_and_one_that_passes_over_nothing_is_caught(tmp_path):
    (tmp_path / "Sources").mkdir()
    gates = tmp_path / "checks"
    gates.mkdir()
    (gates / "check_blind.sh").write_text(
        "#!/usr/bin/env bash\n"
        "n=$(find Sources -type f | wc -l | tr -d ' ')\n"
        'echo "ok $n files clean"\n'
        "exit 0\n"
    )
    (gates / "check_guarded.sh").write_text(
        "#!/usr/bin/env bash\n"
        "n=$(find Sources -type f | wc -l | tr -d ' ')\n"
        'if [ "$n" = 0 ]; then echo "x scanned nothing"; exit 1; fi\n'
        "exit 0\n"
    )
    blind, unrunnable = check_empty_scope.sweep(
        str(tmp_path), "checks", ["check_blind.sh", "check_guarded.sh"]
    )
    assert "check_blind.sh" in blind, (blind, unrunnable)
    assert "check_guarded.sh" not in blind, (blind, unrunnable)


def test_a_repo_whose_gates_are_all_shell_is_still_swept(tmp_path):
    """The gate DIRECTORY is found by what it holds: a repo with no Python gate at all was not
    swept, so its shell gates were not either."""
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / "check_blind.sh").write_text("#!/usr/bin/env bash\necho 'ok 0 files clean'\n")
    assert check_empty_scope.gate_dir(str(repo)) == str(repo / "tools")
    assert check_empty_scope.gate_names(str(repo / "tools")) == ["check_blind.sh"]


def test_a_shell_gate_that_demands_an_argument_is_never_scored_as_a_pass(tmp_path):
    """The Python shape is argparse's (`usage:` AND `error:`); a shell gate prints `usage:` and
    exits 2. Scored as a refusal it landed in the healthy column having looked at nothing."""
    gates = tmp_path / "checks"
    gates.mkdir()
    (gates / "check_demanding.sh").write_text(
        '#!/usr/bin/env bash\necho "usage: check_demanding.sh <built-binary>" >&2\nexit 2\n'
    )
    blind, unrunnable = check_empty_scope.sweep(str(tmp_path), "checks", ["check_demanding.sh"])
    assert "check_demanding.sh" not in blind, blind
    # The reason names the SHELL's own usage line: run by python3 the same file died with a
    # SyntaxError, which the Python shape also matched -- for the wrong reason, and by accident.
    assert "check_demanding.sh <built-binary>" in unrunnable["check_demanding.sh"], unrunnable


def test_a_shell_forwarder_is_named_and_not_swept(tmp_path):
    """Phase N3 kept a forwarder per retired checker so a consumer calling the file by path keeps
    working; `checks/check_shell_lint.sh` is the shell one. Swept as a gate it would fail in the
    skeleton for want of its dispatcher -- a verdict about the sweep, not about the check."""
    gates = tmp_path / "checks"
    gates.mkdir()
    (gates / "check_shell_lint.sh").write_text(
        "#!/usr/bin/env bash\n"
        "{ # parse-guard -- bash reads this group whole before running it\n"
        'exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../gates/goh.sh" shell-lint "$@"\n'
        "exit\n"
        "} # parse-guard\n"
    )
    (gates / "check_real.sh").write_text("#!/usr/bin/env bash\necho 'x scanned nothing'\nexit 1\n")
    assert check_empty_scope.forwarders(str(gates)) == ["check_shell_lint.sh"]
    assert check_empty_scope.gate_names(str(gates)) == ["check_real.sh"]
