"""goh's own hooks judge a seeded file with HEAD's goh, run from an export with no history (GOH.HOOKSEED).

Every gate entry point re-runs itself from `git archive` of HEAD (gates/_from_head.sh, C4). An
archive has no history, so a root-keyed seed lookup asked of it found no root, and goh's own
pre-commit refused every seeded file a commit touched: ad80028 (GOH.PLURALHEDGE) went in with
`GOH_LIVE=1`. The export now writes the root it was cut from (`.goh-root`), and
`gitutil::root_commit` answers an export from it (else from the live checkout, GOH_LIVE_ROOT).

Planted on a scratch clone of the gates, as the hook meets it: no GOH_LIVE, an empty private store.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_gate_environment import _clean_checkout_of_todays_gates
from test_gates_from_head import _env


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip


@pytest.fixture(scope="module")
def gates(tmp_path_factory) -> Path:
    return _clean_checkout_of_todays_gates(tmp_path_factory.mktemp("hookseed"))


def _hedge(gates: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    store = tmp_path / "store"
    store.mkdir(exist_ok=True)
    return subprocess.run(["bash", str(gates / "gates" / "goh.sh"), "plural-hedge", "--staged"],
                          cwd=gates, env=_env(tmp_path, GOH_SEED_DIR=str(store)),
                          capture_output=True, text=True, timeout=120, check=False,
                          stdin=subprocess.DEVNULL)  # fmt: skip


def test_the_export_names_the_root_it_was_cut_from(gates: Path, tmp_path: Path) -> None:
    r = _hedge(gates, tmp_path)
    assert r.returncode in (0, 1), r.stdout + r.stderr
    export = tmp_path / "head-cache" / _git(gates, "rev-parse", "HEAD")
    assert (export / ".goh-head").is_file(), "the step did not run from HEAD's export"
    want = _git(gates, "rev-list", "--max-parents=0", "HEAD")
    assert (export / ".goh-root").is_file(), "the export carries no root: its seeds key by nothing"
    assert (export / ".goh-root").read_text().split() == want.split()


def test_a_seeded_file_passes_the_hook_in_gohs_own_repo(gates: Path, tmp_path: Path) -> None:
    entry = json.loads((gates / "plural_hedge_seed.json").read_text())["entries"][0]
    seeded = gates / entry["path"]
    before = seeded.read_text()
    try:
        seeded.write_text(before + "\n# GOH.HOOKSEED probe\n")
        _git(gates, "add", entry["path"])
        r = _hedge(gates, tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "seeded" in r.stdout + r.stderr and entry["path"] in r.stderr
    finally:
        seeded.write_text(before)
        _git(gates, "add", entry["path"])
