"""scripts/ship_preflight_audit.py: the ratchet of estate release kits declaring no ship preflight.

GOH.SHIPPREFLIGHT: a precondition a kit checks after `goh ship`'s push belongs in
GOH_SHIP_PREFLIGHT, which ship runs before it. The audit names every repo declaring
GOH_SHIP_RELEASE without one; a seeded repo warns, an unseeded one is red, and a seeded repo that
is no longer a finding is stale and red, so the seed only shrinks.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

AUDIT = REPO_ROOT / "scripts" / "ship_preflight_audit.py"


def _repo(root: Path, name: str, gatesrc: str) -> None:
    (root / name / ".git").mkdir(parents=True)
    (root / name / ".gatesrc").write_text(gatesrc)


def _estate(tmp_path: Path) -> Path:
    root = tmp_path / "Projects"
    _repo(root, "late", "GOH_SHIP_RELEASE='tools/ship_release.sh {version}'\n")
    _repo(root, "early", "GOH_SHIP_RELEASE='tools/r.sh {version}'\n"
                         "GOH_SHIP_PREFLIGHT='tools/r.sh --check {version}'\n")  # fmt: skip
    _repo(root, "games/untagged", "GOH_LAND_GATE=make\n")
    return root


def _audit(tmp_path: Path, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(GOH_SEED_DIR=str(tmp_path / "seeds"),
                       GOH_ESTATE_STATE=str(tmp_path / "st.json"))  # fmt: skip
    return subprocess.run([sys.executable, str(AUDIT), "--root", str(root), *args], env=env,
                          capture_output=True, text=True, timeout=60, check=False,
                          stdin=subprocess.DEVNULL)  # fmt: skip


def _seed(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "seeds" / "ship_preflight.json").read_text())["repos"]


def test_the_seed_is_todays_list_and_a_seeded_finding_only_warns(tmp_path):
    root = _estate(tmp_path)
    assert _audit(tmp_path, root).returncode == 2  # no seed yet: a precondition, never a green
    assert _audit(tmp_path, root, "--seed").returncode == 0
    assert list(_seed(tmp_path)) == ["late"]
    r = _audit(tmp_path, root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "late" in r.stdout + r.stderr


def test_a_new_kit_without_a_preflight_is_red(tmp_path):
    root = _estate(tmp_path)
    _audit(tmp_path, root, "--seed")
    _repo(root, "fresh", "GOH_SHIP_RELEASE='tools/release.sh'\n")
    r = _audit(tmp_path, root)
    assert r.returncode == 1 and "fresh" in r.stdout + r.stderr, r.stdout + r.stderr


def test_a_seeded_repo_that_declares_its_preflight_is_stale_and_red(tmp_path):
    root = _estate(tmp_path)
    _audit(tmp_path, root, "--seed")
    with (root / "late" / ".gatesrc").open("a") as f:
        f.write("GOH_SHIP_PREFLIGHT='tools/ship_release.sh --check {version}'\n")
    r = _audit(tmp_path, root)
    assert r.returncode == 1 and "late" in r.stdout + r.stderr and "delete" in r.stdout + r.stderr


def test_the_environment_does_not_mask_a_repos_own_gatesrc(tmp_path):
    root = _estate(tmp_path)
    _audit(tmp_path, root, "--seed")
    env = hermetic_env(GOH_SEED_DIR=str(tmp_path / "seeds"), GOH_SHIP_PREFLIGHT="true",
                       GOH_ESTATE_STATE=str(tmp_path / "st.json"))  # fmt: skip
    env_set = subprocess.run([sys.executable, str(AUDIT), "--root", str(root)], env=env,
                             capture_output=True, text=True, timeout=60, check=False,
                             stdin=subprocess.DEVNULL)  # fmt: skip
    assert env_set.returncode == 0, (
        env_set.stdout + env_set.stderr
    )  # `late` is still the seeded one
