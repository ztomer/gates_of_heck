"""The suite's verdict record is keyed on the REAL toolchain and written by the suite alone.

GOH.VERDICTCARRY3. Two defects kept main's full-suite record unreachable and the store unsound:

  * tests/_drift_guard.py puts a per-run cargo shim (goh-drift-cargo.XXXX) first on PATH, and
    lib/gate_identity.py `tools()` fingerprinted the shim: every suite run had its own tools key
    (tree cad06549 held 6 records under 6 keys), so same-tree reuse and carry never matched inside
    the suite and no lookup outside it ever matched a record the suite wrote. The shim names the
    binary it runs; the identity keys THAT binary (lib/tool_shim.py).
  * A pytest session a test starts with the suite's environment (test_temp_claimable's session-start
    probe, test_hook_git_env's) inherited the land's keys and wrote 1-pass 'pre-land' and 'land
    gate' records for the REAL tree into the REAL store, where a carry could trust them. The
    suite's own session takes the keys OUT of the environment (tests/_land_verdicts.py), so a
    session a test starts sees only the keys that test names.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_identity
import reuse
import tool_shim

# -- one tools key per tree, inside the suite and out -------------------------------------------

_KEY = (
    "import sys; sys.path[:0] = sys.argv[1:3]; "
    "{guard}import gate_identity; print(gate_identity.suite_tools())"
)


def _key(env: dict[str, str], drift: bool) -> str:
    code = _KEY.format(guard="import _drift_guard; " if drift else "")
    r = subprocess.run(
        [sys.executable, "-c", code, str(REPO_ROOT / "tests"), str(REPO_ROOT / "lib")],
        env=env, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
        timeout=patience(60),
    )  # fmt: skip
    assert r.returncode == 0 and r.stdout.strip(), r.stdout + r.stderr
    return r.stdout.strip()


def test_every_suite_run_and_the_outside_lookup_share_one_tools_key(tmp_path: Path) -> None:
    """Two suite runs on one tree, and `reuse.py full-suite` outside any suite, ask ONE key: each
    run puts its own drift shim first on PATH, which names the cargo it runs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "cargo").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "cargo").chmod(0o755)
    env = hermetic_env(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    one, two, outside = _key(env, drift=True), _key(env, drift=True), _key(env, drift=False)
    assert one == two == outside


def test_a_shim_naming_its_binary_is_keyed_as_that_binary_through_a_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nested suite's shim runs the outer suite's shim, which runs the real tool: both resolve."""
    real = tmp_path / "real" / "shellcheck"
    real.parent.mkdir()
    real.write_text("#!/bin/sh\nexit 0\n")
    real.chmod(0o755)
    outer, inner = tmp_path / "outer", tmp_path / "inner"
    for d, target in ((outer, real), (inner, outer / "shellcheck")):
        d.mkdir()
        (d / "shellcheck").write_text(f"#!/bin/sh\n{tool_shim.SHIM_MARK}{target}\nexec x\n")
        (d / "shellcheck").chmod(0o755)
    monkeypatch.setenv("PATH", f"{real.parent}{os.pathsep}{os.environ['PATH']}")
    plain = [ln for ln in gate_identity.tools() if ln.startswith("tool shellcheck ")]
    monkeypatch.setenv("PATH", f"{inner}{os.pathsep}{outer}{os.pathsep}{os.environ['PATH']}")
    shimmed = [ln for ln in gate_identity.tools() if ln.startswith("tool shellcheck ")]
    assert shimmed == plain and str(real) in plain[0], (shimmed, plain)


# -- a session a test starts never records into the land's store ----------------------------------

OUTER_CONFTEST = f"""
import sys
sys.path.insert(0, {str(REPO_ROOT / "tests")!r})
import _land_verdicts as lv
lv.identity = lambda root=None: "ID"
lv.here = lambda root=None: ("T1", "TOOLS")
lv.trees = lambda root, pre: []

def pytest_configure(config):
    config.pluginmanager.register(lv, "goh-land-verdicts")
"""

# The way test_temp_claimable starts its probe session: the whole environment, unscrubbed.
OUTER_TEST = """
import os, subprocess, sys

def test_outer():
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "test_child.py"], env={**os.environ}, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
"""

LAND_KEYS = ("GOH_LAND_VERDICTS_RECORD", "GOH_LAND_VERDICTS_REUSE", "GOH_LAND_VERDICTS_STORE")
CHILD_TEST = f"""
import os

def test_child():
    leaked = [k for k in {(*LAND_KEYS, "GOH_REUSE_DIR")!r} if k in os.environ]
    assert not leaked, leaked
"""


@pytest.mark.parametrize("key", ["GOH_LAND_VERDICTS_RECORD", "GOH_LAND_VERDICTS_REUSE"])
def test_a_session_a_test_starts_never_records_into_the_lands_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    d = tmp_path / "s"
    d.mkdir()
    (d / "conftest.py").write_text(OUTER_CONFTEST)
    (d / "test_outer.py").write_text(OUTER_TEST)
    (d / "test_child.py").write_text(CHILD_TEST)
    store = tmp_path / "store"
    land = {key: str(d / "rec.json"), "GOH_LAND_VERDICTS_STORE": str(store)}
    env = hermetic_env(drop_git=True, GOH_LOOPCTL=str(d / "no-loopctl"), **land)
    env.pop("GOH_REUSE_DIR")  # each test's own (tests/host_locks.py): a land names its store
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA", "test_outer.py"],
        cwd=d, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False, env=env,
        timeout=patience(120),
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    monkeypatch.setenv("GOH_REUSE_DIR", str(store))
    rec, why = reuse.lookup_suite(REPO_ROOT, "T1", "TOOLS")
    assert rec is not None, why
    assert set(rec["passed"]) == {"test_outer.py::test_outer"}, rec["passed"]
