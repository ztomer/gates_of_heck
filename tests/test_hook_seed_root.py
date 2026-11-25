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
from _todays_gates import _clean_checkout_of_todays_gates
from _tree_clone import run_head_cache
from test_gates_from_head import _env


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip


@pytest.fixture(scope="module")
def gates(tmp_path_factory) -> Path:
    return _clean_checkout_of_todays_gates(tmp_path_factory.mktemp("hookseed"), rust=False)


def _hedge(gates: Path, tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    store = tmp_path / "store"
    store.mkdir(exist_ok=True)
    return subprocess.run(["bash", str(gates / "gates" / "goh.sh"), "plural-hedge", "--staged"],
                          cwd=gates, env=_env(tmp_path, GOH_SEED_DIR=str(store), **env),
                          capture_output=True, text=True, timeout=120, check=False,
                          stdin=subprocess.DEVNULL)  # fmt: skip


def test_the_export_names_the_root_it_was_cut_from(gates: Path, tmp_path: Path) -> None:
    r = _hedge(gates, tmp_path, GOH_HEAD_CACHE=str(run_head_cache()))  # the commit's one, the run's
    assert r.returncode in (0, 1), r.stdout + r.stderr
    export = run_head_cache() / _git(gates, "rev-parse", "HEAD")
    assert (export / ".goh-head").is_file(), "the step did not run from HEAD's export"
    want = _git(gates, "rev-list", "--max-parents=0", "HEAD")
    assert (export / ".goh-root").is_file(), "the export carries no root: its seeds key by nothing"
    assert (export / ".goh-root").read_text().split() == want.split()


def test_a_seeded_file_passes_the_hook_in_gohs_own_repo(gates: Path, tmp_path: Path) -> None:
    # goh's own seed is empty (GOH.HEDGEOWN), so the clone seeds a planted hedge of its own and
    # commits both, the shape the hook met: HEAD's export carries the seed, keyed by the root.
    rel = "checks/_tree_stamp.py"
    seeded = gates / rel
    before = seeded.read_text()
    seed_file = gates / "plural_hedge_seed.json"
    seed_before = seed_file.read_text()
    seed = json.loads(seed_before)
    root = _git(gates, "rev-list", "--max-parents=0", "HEAD").split()[0]
    seed["entries"] = [{"root": root, "repo": "gates_of_heck", "path": rel, "count": 1,
                        "reason": "unreviewed: the GOH.HOOKSEED probe"}]  # fmt: skip
    try:
        seeded.write_text(before + 'print(f"{n} step' + '(s) ran")\n')
        seed_file.write_text(json.dumps(seed, indent=2) + "\n")
        _git(gates, "add", rel, "plural_hedge_seed.json")
        _git(
            gates,
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "seed",
            "--no-verify",
        )
        seeded.write_text(seeded.read_text() + "\n# GOH.HOOKSEED probe\n")
        _git(gates, "add", rel)
        r = _hedge(gates, tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "seeded" in r.stdout + r.stderr and rel in r.stderr
    finally:
        seeded.write_text(before)
        seed_file.write_text(seed_before)
        _git(gates, "add", rel, "plural_hedge_seed.json")
