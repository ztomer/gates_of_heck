"""Every way the machine runs ruff is the same ruff (GOH.TOOLHYGIENE, rule 17).

2026-11-11: `python3 -m ruff` was 0.17.0 (the house interpreter, the Python gates' runner) while
PATH's `ruff` was 0.17.4 (~/.local/bin, /opt/homebrew/bin: pyformat's fallback, what an agent
types). gates/ruff_agree.py refuses the split, naming each invocation and the upgrade.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env

AGREE = REPO_ROOT / "gates" / "ruff_agree.py"


def _fake(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho "ruff {version}"\n')
    path.chmod(0o755)
    return path


def _py(tmp_path: Path, version: str) -> Path:
    """An interpreter whose `-m ruff --version` says `version`."""
    return _fake(tmp_path / "py" / "python3", version)


def _run(tmp_path: Path, py: Path, *dirs: Path) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(GOH_PY=str(py), PATH=os.pathsep.join([*map(str, dirs), "/usr/bin:/bin"]))
    return subprocess.run([sys.executable, str(AGREE)], env=env, capture_output=True, text=True,
                          timeout=60, stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def test_the_split_of_2026_11_11_is_refused_naming_both(tmp_path):
    local = _fake(tmp_path / "local" / "ruff", "0.17.4")
    r = _run(tmp_path, _py(tmp_path, "0.17.0"), local.parent)
    assert r.returncode == 1, r.stderr
    assert "ruff 0.17.0" in r.stderr and "ruff 0.17.4" in r.stderr, r.stderr
    assert "-m pip install -U ruff" in r.stderr, r.stderr  # the older one's upgrade


@pytest.mark.parametrize("path_versions", [(), ("0.17.4",), ("0.17.4", "0.17.4")])
def test_one_version_everywhere_agrees(tmp_path, path_versions):
    dirs = [_fake(tmp_path / f"d{i}" / "ruff", v).parent for i, v in enumerate(path_versions)]
    r = _run(tmp_path, _py(tmp_path, "0.17.4"), *dirs)
    assert r.returncode == 0, r.stderr


def test_two_rufs_on_path_that_disagree_are_refused(tmp_path):
    a = _fake(tmp_path / "a" / "ruff", "0.17.4").parent
    b = _fake(tmp_path / "b" / "ruff", "0.16.9").parent
    r = _run(tmp_path, _py(tmp_path, "0.17.4"), a, b)
    assert r.returncode == 1 and "ruff 0.16.9" in r.stderr, r.stderr


def test_the_suite_preflight_runs_it():
    assert "gates/ruff_agree.py" in (REPO_ROOT / "tools" / "pytest.sh").read_text()


# The hosted runner (GOH.CIRUFF): run 43139901867 failed goh's rust coverage step's pytest
# export on "ruff: 3 invocations, 2 versions" -- the manifest's `brew install ruff` and its unpinned
# pip row gave the runner two, and the split read as a coverage loss, not a provisioning one. The
# workflow now installs ONE ruff, its pin, and asks `ruff_agree.py --pin` in that same step.
PIN = "0.17.4"


def _run_pin(py: Path, pin: str, *dirs: Path) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(GOH_PY=str(py), PATH=os.pathsep.join([*map(str, dirs), "/usr/bin:/bin"]))
    return subprocess.run([sys.executable, str(AGREE), "--pin", pin], env=env, capture_output=True,
                          text=True, timeout=60, stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def test_the_pin_holds_when_every_ruff_is_it(tmp_path):
    on_path = _fake(tmp_path / "bin" / "ruff", PIN).parent
    assert _run_pin(_py(tmp_path, PIN), PIN, on_path).returncode == 0


def test_rufs_that_agree_on_another_version_break_the_pin(tmp_path):
    """Agreeing is not enough on the runner: two 0.17.3s are not the pinned 0.17.4."""
    on_path = _fake(tmp_path / "bin" / "ruff", "0.17.3").parent
    r = _run_pin(_py(tmp_path, "0.17.3"), PIN, on_path)
    assert r.returncode == 1, r.stderr
    assert f"pinned ruff {PIN}" in r.stderr and "ruff 0.17.3" in r.stderr, r.stderr


def test_a_second_ruff_the_runner_image_carries_breaks_the_pin(tmp_path):
    house = _fake(tmp_path / "house" / "ruff", PIN).parent
    image = _fake(tmp_path / "image" / "ruff", "0.16.9").parent
    r = _run_pin(_py(tmp_path, PIN), PIN, house, image)
    assert r.returncode == 1 and "ruff 0.16.9" in r.stderr, r.stderr
    assert str(image / "ruff") in r.stderr, "names the stray one: " + r.stderr


def test_the_pin_needs_the_house_interpreters_ruff(tmp_path):
    """No `-m ruff` at all is not agreement under a pin: the Python gates run exactly that."""
    py = tmp_path / "py" / "python3"
    py.parent.mkdir()
    py.write_text("#!/bin/sh\nexit 1\n")
    py.chmod(0o755)
    r = _run_pin(py, PIN)
    assert r.returncode == 1 and "-m ruff" in r.stderr, r.stderr


def _install_step() -> str:
    """The Install step's `run: |` block, read as text: the hosted runner has no PyYAML (v0.101.0's
    run 43147543825 went red on `import yaml`), and a test of the runner must run on the runner."""
    lines = (REPO_ROOT / ".github" / "workflows" / "gate.yml").read_text().splitlines()
    [at] = [i for i, x in enumerate(lines) if x.strip().startswith("- name: Install")]
    assert lines[at + 1].strip() == "run: |", lines[at + 1]
    indent = len(lines[at + 1]) - len(lines[at + 1].lstrip()) + 2
    body = []
    for x in lines[at + 2 :]:
        if x.strip() and len(x) - len(x.lstrip()) < indent:
            break
        body.append(x[indent:])
    return "\n".join(body).rstrip() + "\n"


def test_the_workflow_installs_one_pinned_ruff_and_asks_the_pin_before_the_gate():
    import re

    wf = (REPO_ROOT / ".github" / "workflows" / "gate.yml").read_text()
    m = re.search(r'GOH_CI_RUFF: "(\d+\.\d+\.\d+)"', wf)
    assert m, "gate.yml pins ruff to a named release in GOH_CI_RUFF"
    run = _install_step()
    assert run.count('install "ruff==$GOH_CI_RUFF"') == 1, run
    # The manifest's own ruff (brew, and an unpinned pip) is filtered out of what the step runs:
    # the step's filter, applied to the real manifest, leaves no ruff and every other tool.
    [line] = [x for x in run.splitlines() if x.rstrip().endswith("| bash -eux")]
    filt = line.split('<<<"$cmds" |', 1)[1].rsplit("| bash -eux", 1)[0]
    cmds = subprocess.run([sys.executable, str(REPO_ROOT / "gates" / "required_tools.py"), "--repo",
                           str(REPO_ROOT), "--install"], capture_output=True, text=True,
                          check=True, stdin=subprocess.DEVNULL).stdout  # fmt: skip
    assert re.search(r"\bruff\b", cmds), "the manifest installs ruff: the filter has work to do"
    left = subprocess.run(["bash", "-c", f"grep -v '^#' | {filt}"], input=cmds,
                          capture_output=True, text=True, check=True).stdout  # fmt: skip
    assert not re.search(r"\bruff\b", left), left
    assert "shellcheck" in left and "pytest" in left, "every other tool is still installed: " + left
    assert 'gates/ruff_agree.py --pin "$GOH_CI_RUFF"' in run, run
    assert wf.index("ruff_agree.py") < wf.index("./tools/gate.sh --full")
