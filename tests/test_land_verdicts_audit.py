"""A land gate's same-tree reuse under the reuse audit (GOH.REUSEAUDIT2, tests/_land_audit.py): each
node the pre-land run passed on this tree is one use of the `test` path, decided once by the
controller, so every xdist worker skips alike; a sampled node runs anyway and a repeated failure
turns `test` off.

The calibration is a PLANTED stale record: the pre-land record says test_planted passed on this
tree, and it fails here (an input the record's identity lacks). The shadow must run it red, and
the next gate must reuse nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from test_verdict_carry import SESSION_CONFTEST, TEST_T, session, store  # noqa: F401

sys.path.insert(0, str(REPO_ROOT / "lib"))
import reuse_audit

EVERY_TEST = """
if os.environ.get("EVERY_TEST"):
    n = int(os.environ["EVERY_TEST"])
    reuse.PATHS["test"] = dataclasses.replace(reuse.PATHS["test"], every=n)
"""
PRE = {"test_t.py::test_ok": False, "test_t.py::test_planted": False}


def _gate(d: Path, at: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    """test_verdict_carry's gate session, its pre-land record `pre.json` (the fixture's)."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA", *args],
        cwd=d, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
        env=hermetic_env(drop_git=True, GOH_REUSE_DIR=str(at), CHOSEN="test_s.py test_t.py",
                         GOH_LOOPCTL=str(d / "no-loopctl"),
                         GOH_LAND_VERDICTS_REUSE=str(d / "pre.json"), **env),
    )  # fmt: skip


@pytest.fixture
def pre(session: Path) -> Path:  # noqa: F811
    """The session's conftest with the `test` path's rate seam, and a pre-land record that
    passed both of test_t.py's nodes on this tree (identity ID, the fixture's)."""
    (session / "conftest.py").write_text(SESSION_CONFTEST + EVERY_TEST)
    (session / "test_s.py").write_text("def test_pass():\n    pass\n")
    (session / "test_t.py").write_text(TEST_T)
    rec = session / "pre.json"
    rec.write_text(json.dumps({"identity": "ID", "sha": "c1" * 20, "passed": PRE}))
    return rec


@pytest.mark.parametrize("workers", [[], ["-n", "2"]], ids=["serial", "xdist"])
def test_a_planted_stale_same_tree_reuse_is_run_by_its_shadow_and_turns_test_off(
    session: Path,  # noqa: F811
    store: Path,  # noqa: F811
    pre: Path,
    workers: list[str],
) -> None:
    shadow = _gate(session, store, *workers, EVERY_TEST="1")
    out = shadow.stdout
    assert "2 tests passed by the pre-land run are reused" in out, out
    assert "2 of them RUN anyway: the audit's shadow" in out, out
    assert "PASSED test_t.py::test_ok" in out and "FAILED test_t.py::test_planted" in out, out
    rec = reuse_audit.state("test")
    assert rec["state"] == "off" and rec["cause"] == "disagree", rec
    assert rec["evidence"]["fields"]["node"] == "test_t.py::test_planted"
    assert rec["evidence"]["again"] == {"reused": "green", "fresh": "red"}, rec
    assert rec["window"]["uses"] == 2 and rec["window"]["agreed"] == 1, rec
    after = _gate(session, store, *workers)
    assert "none reused after all: the audit turned `test` off" in after.stdout, after.stdout
    assert "PASSED test_t.py::test_ok" in after.stdout, after.stdout


def test_an_unsampled_same_tree_reuse_still_skips(session: Path, store: Path, pre: Path) -> None:  # noqa: F811
    r = _gate(session, store)
    assert r.returncode == 0, r.stdout  # the planted node skipped: the lie the sample exists for
    assert "test_t.py::test_planted" not in r.stdout and "RUN anyway" not in r.stdout, r.stdout
    assert reuse_audit.state("test")["window"]["uses"] == 2
