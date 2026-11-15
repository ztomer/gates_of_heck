"""A green re-run of a failed verify finishes the ship: result, then done step (GOH.SHIPREVERIFY).

goh v0.116.0's ship failed at verify on a transient: a `bin/goh.*.staging` another goh build wrote
and removed read as "working tree: 1 uncommitted" on the card. Re-running `tools/ship.py --verify
RESULT` read the card green but left the result `failed`, so `loopctl done --shipped` refused it
and the coordinator recorded nine items by hand. A re-run of a result that failed AT VERIFY now
resumes the ship there: verify again, as the worker runs it, and on green the result reads `ok` and
the done step runs the same `loopctl done ID --shipped RESULT` the ship would have. Still red, the
result stays failed at verify with the new tail. The loopctl here is test_ship's fake, its card
failing while a transient file exists.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import test_ship
from test_ship import FAKE_LOOPCTL, SHIP, _calls, _env, _result, _ship

shipped = test_ship.shipped  # the fixture, by assignment (an import would be shadowed by each arg)
# A transient on the card: it fails while card.transient sits beside its call log (tmp_path).
# The live checkout's own strays no longer reach it: verify judges a clean export of the shipped
# sha (tests/test_ship_export.py).
DIRTY_CARD = """\
import os, sys
if sys.argv[1] == "verify" and os.path.exists(
        os.path.join(os.path.dirname(os.environ["FAKE_LOOPCTL_LOG"]), "card.transient")):
    os.environ.update(FAKE_VERIFY_RC="1", FAKE_CARD_ERR="working tree: 1 uncommitted")
"""
TRANSIENT = "card.transient"  # under tmp_path; the install step makes it, as a stray once did


def _failed_at_verify(repo: Path, tmp_path: Path, *args: str) -> dict:
    (tmp_path / "loopctl.py").write_text(DIRTY_CARD + FAKE_LOOPCTL)
    install = f"echo half > {tmp_path / TRANSIENT}"
    r = _ship(repo, tmp_path, "--wait", "--item", "X.ONE", *args, GOH_SHIP_INSTALL=install)
    assert r.returncode == 1, r.stdout + r.stderr
    res = _result(tmp_path)
    assert (res["status"], res["failed_step"]) == ("failed", "verify"), res
    assert not [c for c in _calls(tmp_path) if c[0] in ("done", "progress")]
    return res


def _reverify(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SHIP), "--verify", str(tmp_path / "ship.json")],
                          env=_env(tmp_path), capture_output=True, text=True, timeout=120,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def test_a_green_reverify_records_the_ship_ok_and_runs_the_done_step(shipped, tmp_path):
    _failed_at_verify(shipped, tmp_path)
    (tmp_path / TRANSIENT).unlink()  # the transient is gone
    r = _reverify(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["status"] == "ok", res
    assert "failed_step" not in res and "tail" not in res, res
    assert [(s["name"], s["status"]) for s in res["steps"]] == [
        ("push", "ok"),
        ("release", "ok"),
        ("install", "ok"),
        ("verify", "ok"),
        ("done", "ok"),
    ]
    assert [s["status"] for s in res["retried"]] == ["failed"]  # the first verify is kept, apart
    assert ["done", "X.ONE", "--shipped", str(tmp_path / "ship.json")] in _calls(tmp_path)
    verify_log = Path(res["steps"][3]["log"]).read_text()
    assert "working tree" not in verify_log, verify_log  # done reads the GREEN card's log


def test_a_reverify_still_red_leaves_the_ship_failed_at_verify(shipped, tmp_path):
    _failed_at_verify(shipped, tmp_path)
    r = _reverify(tmp_path)  # the stray is still there
    assert r.returncode == 1, r.stdout + r.stderr
    res = _result(tmp_path)
    assert (res["status"], res["failed_step"]) == ("failed", "verify"), res
    assert any("working tree" in ln for ln in res["tail"]), res["tail"]
    assert [s["name"] for s in res["steps"]] == ["push", "release", "install", "verify"]
    assert not [c for c in _calls(tmp_path) if c[0] in ("done", "progress")]


def test_a_reverified_slice_records_progress(shipped, tmp_path):
    _failed_at_verify(shipped, tmp_path, "--progress")
    (tmp_path / TRANSIENT).unlink()
    r = _reverify(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _calls(tmp_path)[-1][:3] == ["progress", "X.ONE", "--shipped"], _calls(tmp_path)


def test_a_result_that_failed_elsewhere_is_only_read(shipped, tmp_path):
    """Only a ship that stopped AT verify resumes there: one that failed at its release never
    released, and a card read over it records nothing."""
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE", GOH_SHIP_RELEASE="exit 3")
    assert r.returncode == 1, r.stdout + r.stderr
    before = (tmp_path / "ship.json").read_text()
    _reverify(tmp_path)
    assert (tmp_path / "ship.json").read_text() == before
    assert json.loads(before)["failed_step"] == "release"
    assert not [c for c in _calls(tmp_path) if c[0] in ("done", "progress")]
