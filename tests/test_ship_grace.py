"""goh ship carries the release canary's grace-end finding (GOH.CANARYGRACE).

The canary runs in goh's push gate, which the release step's push runs; it names every consumer the
grace's end turns red (scripts/release_canary.py, `grace_over`) without refusing the release. The
ship result carries that count and those names for the commit it shipped, `goh ship --status` says
them, and the verify card -- the log `loopctl done --shipped` keeps its `·` lines from -- says them.
The kit here stands in for the push gate: it writes the canary's record for the commit it tags.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git
from test_ship import SHIP, _env, _result, _ship, make_shipped

RECORD = """\
import json, subprocess, sys
sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                     stdin=subprocess.DEVNULL, check=True).stdout.strip()
grace = {"ran": True, "count": 1, "over_from": "2026-11-23", "errors": [], "seconds": 9.0,
         "turned_red": {"games/planted": [{"step": "no bare open of an app", "found": []}]}}
sys.path.insert(0, sys.argv[2])
import small_stores  # the canary's record is goh.db's kv, as scripts/release_canary.py writes it
small_stores.put(sys.argv[1], {"release_canary": {"head_sha": sha, "grace_over": grace}})
"""
TAG = 'git tag "v$GOH_SHIP_VERSION" && git push -q origin "v$GOH_SHIP_VERSION"'


@pytest.fixture
def shipped(repo: Path, tmp_path: Path) -> Path:
    return make_shipped(repo, tmp_path)


def _gates_tree(repo: Path) -> None:
    """The repo ships a release canary, as gates_of_heck does."""
    (repo / "scripts").mkdir()
    (repo / "scripts" / "release_canary.py").write_text("# the canary\n")
    git(repo, "add", "scripts")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "feat: canary")


def test_the_result_and_the_card_carry_the_grace_end_count_and_names(shipped, tmp_path):
    state = tmp_path / "estate_audit.json"
    (tmp_path / "record.py").write_text(RECORD)
    kit = f"{sys.executable} {tmp_path / 'record.py'} {state} {REPO_ROOT / 'lib'} && {TAG}"
    r = _ship(shipped, tmp_path, "--wait", GOH_SHIP_RELEASE=kit, GOH_ESTATE_STATE=str(state))
    assert r.returncode == 0, r.stdout + r.stderr
    res = _result(tmp_path)
    g = res["grace_over"]
    assert g["count"] == 1 and g["repos"] == {"games/planted": ["no bare open of an app"]}, res
    assert g["canary_sha"] == res["sha"], res
    verify = next(s for s in res["steps"] if s["name"] == "verify")
    card = Path(verify["log"]).read_text()
    assert "· grace end (2026-11-23) turns 1 consumer red: games/planted" in card, card
    import subprocess

    st = subprocess.run([sys.executable, str(SHIP), "--status", str(tmp_path / "ship.json")],
                        env=_env(tmp_path), capture_output=True, text=True, check=False,
                        stdin=subprocess.DEVNULL)  # fmt: skip
    assert "grace end (2026-11-23) turns 1 consumer red: games/planted" in st.stdout, st.stdout


def test_a_gates_tree_with_no_record_for_its_commit_says_it_was_not_judged(shipped, tmp_path):
    _gates_tree(shipped)
    state = tmp_path / "estate_audit.json"
    state.write_text(json.dumps({"release_canary": {"head_sha": "0" * 40, "grace_over": {}}}))
    r = _ship(shipped, tmp_path, "--wait", GOH_SHIP_RELEASE=TAG, GOH_ESTATE_STATE=str(state))
    assert r.returncode == 0, r.stdout + r.stderr
    g = _result(tmp_path)["grace_over"]
    assert g["count"] is None and "no release canary record" in g["why"], g


def test_a_repo_that_ships_no_canary_carries_nothing(shipped, tmp_path):
    state = tmp_path / "estate_audit.json"
    r = _ship(shipped, tmp_path, "--wait", GOH_ESTATE_STATE=str(state))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "grace_over" not in _result(tmp_path)
