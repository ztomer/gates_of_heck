"""goh pins the ruff its Python gates judge with (GOH.RUFFVERSION, rule 17).

2026-11-11: divoom_control fc0764e was red on GitHub with SIM102 while its local gate was green:
the local gate ran a stale 0.17.0 and hosted CI installed the newest, 0.17.4. A ruff release moves
rules, so two rufs are two gates. gates/ruff_house.py names ONE release (`RUFF`); a gate whose
runner is machine state (the house interpreter, a .venv, a GOH_PY_RUNNER) refuses any other, naming
the version it found and the runner it found it through; hosted CI and the manifest install
exactly that release.
A runner built from the repo's tracked lock (`uv run --frozen`) is the repo's own pin (GOH.RUFFPIN):
the same bytes on every machine, so it is not second-guessed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "gates"))
from ruff_house import RUFF

sys.path.remove(str(REPO_ROOT / "gates"))


def _runner(into: Path, version: str) -> str:
    """A GOH_PY_RUNNER whose `ruff --version` says `version`; everything else is python3 -m."""
    into.mkdir()
    runner = into / "runner"
    runner.write_text(
        f'#!/bin/sh\n[ "$1 $2" = "ruff --version" ] && {{ echo "ruff {version}"; exit 0; }}\n'
        f'exec "{sys.executable}" -m "$@"\n'
    )
    runner.chmod(0o755)
    return str(runner)


def _staged(repo: Path, runner: str) -> subprocess.CompletedProcess[str]:
    (repo / "m.py").write_text("X = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "m.py"], check=True,
                   env=hermetic_env(drop_git=True), stdin=subprocess.DEVNULL)  # fmt: skip
    env = hermetic_env(GOH_DIR=str(REPO_ROOT), GOH_PY_RUNNER=runner)
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "py_staged.sh"), str(repo)],
        cwd=repo, env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
        check=False,
    )  # fmt: skip


def test_a_ruff_off_the_pin_is_refused_naming_the_version_and_the_runner(tmp_path, repo):
    runner = _runner(tmp_path / "b", "0.17.0")
    got = _staged(repo, runner)
    out = got.stdout + got.stderr
    assert got.returncode != 0, out
    assert "ruff 0.17.0" in out and f"ruff {RUFF}" in out and runner in out, out
    assert f"ruff=={RUFF}" in out, out


def test_the_pinned_ruff_passes(tmp_path, repo):
    got = _staged(repo, _runner(tmp_path / "b", RUFF))
    assert got.returncode == 0, got.stdout + got.stderr


def test_the_pin_is_a_named_release():
    assert re.fullmatch(r"\d+\.\d+\.\d+", RUFF), RUFF


def test_hosted_ci_installs_the_pin():
    got = subprocess.run([sys.executable, str(REPO_ROOT / "gates" / "required_tools.py"), "--pin",
                          "ruff"], capture_output=True, text=True, check=True,
                         stdin=subprocess.DEVNULL)  # fmt: skip
    assert got.stdout.strip() == RUFF, "gate.yml reads `required_tools.py --pin ruff`: the pin"


def test_every_manifest_ruff_row_installs_the_pin():
    sys.path.insert(0, str(REPO_ROOT / "gates"))
    try:
        import required_tools
    finally:
        sys.path.remove(str(REPO_ROOT / "gates"))
    ruff = [r for r in required_tools.rows() if r["tool"] == "ruff"]
    assert ruff, "the manifest installs ruff"
    for r in ruff:
        assert r["install"].endswith(f"ruff=={RUFF}") and r["pin"] == RUFF, r


def test_the_suite_preflight_holds_every_ruff_to_the_pin():
    text = (REPO_ROOT / "tools" / "pytest.sh").read_text()
    pin = r'ruff_agree\.py" --pin "\$\("\$GOH_PY" -S "\$GOH/gates/ruff_house\.py" --pin\)"'
    assert re.search(pin, text), "tools/pytest.sh asks ruff_agree.py --pin with ruff_house's pin"
