"""checks/check_ci_local_parity.py: every repo script CI runs, the local pre-push gate runs too.

Lifted from CadGoose (its ROADMAP G0.16): a push passed all 12 local steps and went red in CI's
Coverage Gate, because that gate was in CI and not in GOH_CI_STEPS. A local gate weaker than CI is
a gate that lets you push red (engineering rule #17). The check reads every `run:` in the
workflows and composite actions, every step of `.gatesrc`'s GOH_CI_STEPS, and names a script only
CI runs. Exemptions are a ratchet; the scan has a floor; both directions are pinned here.
"""

import subprocess
import sys

from conftest import REPO_ROOT, commit_all, write

SCRIPT = "checks/check_ci_local_parity.py"
CHECK = REPO_ROOT / "checks" / "check_ci_local_parity.py"

WORKFLOW = """\
name: ci
on: [push]
jobs:
  gates:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: scripts/lint.sh
      - name: coverage
        run: |
          set -e
          ../tools/coverage_gate.py --floor 95
          echo done
      - run: >
          python3 scripts/folded.py
"""


def fixture(repo, *, steps, workflow=WORKFLOW, extra=""):
    write(repo, ".github/workflows/ci.yml", workflow)
    write(repo, ".gatesrc", f"GOH_CI_STEPS='{steps}'\n{extra}")
    commit_all(repo)


def check(repo, *args):
    return subprocess.run(
        [sys.executable, str(CHECK), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


ALL_LOCAL = "./scripts/lint.sh:python3 tools/coverage_gate.py --floor 95:python3 scripts/folded.py"


def test_a_script_ci_runs_that_no_local_step_runs_is_named(repo):
    """The G0.16 shape: the coverage gate is in CI and absent from GOH_CI_STEPS."""
    fixture(repo, steps="./scripts/lint.sh:python3 scripts/folded.py")
    r = check(repo)
    assert r.returncode == 1
    assert "tools/coverage_gate.py" in r.stderr
    assert "CI runs" in r.stderr
    assert "scripts/lint.sh" not in r.stderr


def test_every_ci_script_run_locally_passes(repo):
    fixture(repo, steps=ALL_LOCAL)
    r = check(repo)
    assert r.returncode == 0, r.stderr
    assert "3 scripts in CI" in r.stdout


def test_inline_block_and_folded_run_forms_are_all_read(repo):
    """An inline `run:`, a `|` block (with a `../` prefix) and a `>` scalar each carry a script."""
    fixture(repo, steps="true")
    r = check(repo, "--list")
    assert r.returncode == 0
    assert r.stdout.split() == [
        "scripts/folded.py",
        "scripts/lint.sh",
        "tools/coverage_gate.py",
    ]


def test_a_composite_action_run_is_ci_too(repo):
    write(
        repo,
        ".github/actions/gate/action.yml",
        "runs:\n  using: composite\n  steps:\n    - run: tools/in_action.sh\n      shell: bash\n",
    )
    fixture(repo, steps=ALL_LOCAL)
    r = check(repo)
    assert r.returncode == 1
    assert "tools/in_action.sh" in r.stderr


def test_an_exemption_with_a_reason_lets_a_ci_only_script_pass(repo):
    fixture(
        repo,
        steps="./scripts/lint.sh:python3 scripts/folded.py",
        extra="GOH_CI_PARITY_EXEMPT='tools/coverage_gate.py: runs only on the CI image'\n",
    )
    r = check(repo)
    assert r.returncode == 0, r.stderr
    assert "1 exempt" in r.stdout


def test_a_stale_exemption_ci_no_longer_runs_is_red(repo):
    fixture(
        repo,
        steps=ALL_LOCAL,
        extra="GOH_CI_PARITY_EXEMPT='scripts/gone.sh: was a packaging step'\n",
    )
    r = check(repo)
    assert r.returncode == 1
    assert "stale exemption scripts/gone.sh" in r.stderr
    assert "CI no longer runs it" in r.stderr


def test_a_stale_exemption_the_local_gate_now_runs_is_red(repo):
    fixture(
        repo,
        steps=ALL_LOCAL,
        extra="GOH_CI_PARITY_EXEMPT='tools/coverage_gate.py: not yet local'\n",
    )
    r = check(repo)
    assert r.returncode == 1
    assert "stale exemption tools/coverage_gate.py" in r.stderr
    assert "local gate runs it now" in r.stderr


def test_an_exemption_without_a_reason_is_a_config_error(repo):
    fixture(repo, steps=ALL_LOCAL, extra="GOH_CI_PARITY_EXEMPT='tools/coverage_gate.py'\n")
    r = check(repo)
    assert r.returncode == 2
    assert "reason" in r.stderr


def test_a_scan_under_its_floor_has_lost_its_subject(repo):
    fixture(repo, steps=ALL_LOCAL, extra="GOH_CI_PARITY_MIN_SCRIPTS=4\n")
    r = check(repo)
    assert r.returncode == 1
    assert "found 3 scripts" in r.stderr
    assert "floor 4" in r.stderr


def test_no_workflows_is_never_a_silent_pass(repo):
    write(repo, ".gatesrc", "GOH_CI_STEPS='true'\n")
    commit_all(repo)
    r = check(repo)
    assert r.returncode == 2
    assert "no .github/workflows" in r.stderr


def test_no_local_steps_is_never_a_silent_pass(repo):
    write(repo, ".github/workflows/ci.yml", WORKFLOW)
    write(repo, ".gatesrc", "GOH_MAX_LINES=500\n")
    commit_all(repo)
    r = check(repo)
    assert r.returncode == 2
    assert "GOH_CI_STEPS" in r.stderr


def test_the_script_dirs_are_configurable(repo):
    """A repo whose gates live in `gates/` names that dir; the default only reads scripts/tools."""
    fixture(
        repo,
        steps="true",
        workflow="jobs:\n  a:\n    steps:\n      - run: gates/x.sh\n",
        extra="GOH_CI_PARITY_DIRS='gates scripts'\nGOH_CI_PARITY_MIN_SCRIPTS=1\n",
    )
    r = check(repo)
    assert r.returncode == 1
    assert "gates/x.sh" in r.stderr


def test_a_gatesrc_that_leaks_no_steps_from_the_environment(repo, monkeypatch):
    """The environment is not configuration: an exported GOH_CI_STEPS is not the repo's."""
    write(repo, ".github/workflows/ci.yml", WORKFLOW)
    write(repo, ".gatesrc", "GOH_MAX_LINES=500\n")
    commit_all(repo)
    monkeypatch.setenv("GOH_CI_STEPS", ALL_LOCAL)
    r = check(repo)
    assert r.returncode == 2


def test_the_probe_proves_the_gate_can_go_red():
    r = subprocess.run(
        [sys.executable, str(CHECK), "--probe"],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stdout + r.stderr
