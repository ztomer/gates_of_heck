"""The house Rust gate audits the lockfile, biting, and holds its ignore list to a ratchet (BACKLOG
6.12; Rust rules 3 and 4).

`cargo audit` exits 0 on an unmaintained crate, so an audit without the deny list passes over the
class it is there to police (calibrated 2026-10-12 on the lock below: plain 0, biting 1). And it
accepts an ignore entry whose advisory no longer fires, silently. The gate runs it with the deny list
on the command line, from the workspace root, and checks/check_audit_ignores.py refuses an exemption
with no reason or one that went stale.

The lockfiles are written by hand: cargo-audit reads names and versions and fetches nothing under
--no-fetch (GOH_DEPS_OFFLINE=1), so no crate is downloaded or built.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from test_rust_gate import CURRENT, _git, mkcrate, run_script

pytestmark = pytest.mark.skipif(
    shutil.which("cargo-audit") is None, reason="cargo-audit not installed"
)

CHECK = REPO_ROOT / "checks" / "check_audit_ignores.py"
UNMAINTAINED = "RUSTSEC-2021-0139"  # ansi_term: unmaintained, a warning cargo-audit exits 0 on
ANSI_TERM = (
    '\n[[package]]\nname = "ansi_term"\nversion = "0.12.1"\n'
    'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
    'checksum = "d52a9bb7ec0cf484c551830a7ce27bd20d67eac647e1befb56b0be4ee39a55d2"\n'
)
OFFLINE = {"GOH_DEPS_OFFLINE": "1", "GOH_PROVEN": "0"}


def _crate(tmp: Path, *, unmaintained: bool, ignore: str | None = None) -> Path:
    c = mkcrate(tmp)
    if unmaintained:
        with (c / "Cargo.lock").open("a") as f:
            f.write(ANSI_TERM)
    if ignore is not None:
        (c / ".cargo").mkdir()
        (c / ".cargo" / "audit.toml").write_text(f"[advisories]\nignore = [\n    {ignore}\n]\n")
    _git(c, "add", "-A")
    return c


def _check(c: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(CHECK), "--no-fetch"],
        cwd=c,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def test_an_unmaintained_crate_fails_the_gate(tmp_path):
    r = run_script(CURRENT, _crate(tmp_path, unmaintained=True), OFFLINE)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "no known advisory" in out and UNMAINTAINED in out, out


def test_a_stale_exemption_fails_the_gate(tmp_path):
    c = _crate(tmp_path, unmaintained=False, ignore=f'"{UNMAINTAINED}",  # ansi_term, until 2027')
    r = run_script(CURRENT, c, OFFLINE)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert f"{UNMAINTAINED} no longer fires" in out, out


def test_a_reasoned_exemption_that_still_fires_passes(tmp_path):
    c = _crate(tmp_path, unmaintained=True, ignore=f'"{UNMAINTAINED}",  # ansi_term, until 2027')
    r = _check(c)
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_exemption_without_a_reason_fails(tmp_path):
    c = _crate(tmp_path, unmaintained=True, ignore=f'"{UNMAINTAINED}",')
    r = _check(c)
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"{UNMAINTAINED} has no `# reason`" in r.stderr, r.stderr


def test_no_config_exempts_nothing_and_passes(tmp_path):
    r = _check(_crate(tmp_path, unmaintained=False))
    assert r.returncode == 0 and "nothing is exempted" in r.stdout, r.stdout + r.stderr


def test_a_tree_without_a_lockfile_is_refused_not_passed(tmp_path):
    """Its subject is the workspace's Cargo.lock: with none, it judged nothing (check_empty_scope)."""
    r = _check(tmp_path)
    assert r.returncode == 1 and "nothing to judge" in r.stderr, r.stdout + r.stderr
