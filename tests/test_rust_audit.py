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


# ── every tracked lock, not only the one beside the manifest (queue GOH.EA2) ────────────────
# monitor's fuzz/, media_server's 31 crates and testkits, koffee_big's core beside an app: each a
# lock of its own workspace, which the gate never read, so each repo hand-wrote an audit step per
# lock through a shell variable the estate audit cannot read.

LOCK_ONLY = "# hand-written: cargo-audit reads the lock alone\nversion = 4\n" + ANSI_TERM
REASONED = f'"{UNMAINTAINED}",  # ansi_term, until 2027'


def _nested(tmp: Path, *, root_ignore: str | None = None, sub_ignore: str | None = None) -> Path:
    """A clean root workspace, and `fuzz/Cargo.lock` -- its own workspace -- carrying ansi_term."""
    c = _crate(tmp, unmaintained=False, ignore=root_ignore)
    (c / "fuzz").mkdir()
    (c / "fuzz" / "Cargo.lock").write_text(LOCK_ONLY)
    if sub_ignore is not None:
        (c / "fuzz" / ".cargo").mkdir()
        (c / "fuzz" / ".cargo" / "audit.toml").write_text(
            f"[advisories]\nignore = [\n    {sub_ignore}\n]\n"
        )
    _git(c, "add", "-A")
    return c


def test_a_lock_of_another_workspace_in_the_repo_is_audited(tmp_path):
    r = run_script(CURRENT, _nested(tmp_path), OFFLINE)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "✗ rust: no known advisory in fuzz/Cargo.lock" in out and UNMAINTAINED in out, out


def test_a_nested_lock_is_judged_by_its_own_audit_toml(tmp_path):
    """monitor's shape: fuzz/.cargo/audit.toml holds what fires there, and still ratchets."""
    r = run_script(CURRENT, _nested(tmp_path, sub_ignore=REASONED), OFFLINE)
    out = r.stdout + r.stderr
    assert "✓ no known advisory in fuzz/Cargo.lock" in out, out
    assert "no longer fires" not in out, out


def test_a_root_audit_toml_governs_the_locks_below_it(tmp_path):
    """media_server's shape: one policy at the root for every crate. Its entry fires against a
    lock below it, not the root's own, so it is NOT stale; removing it turns the gate red."""
    c = _nested(tmp_path, root_ignore=REASONED)
    r = run_script(CURRENT, c, OFFLINE)
    out = r.stdout + r.stderr
    assert "✓ no known advisory in fuzz/Cargo.lock" in out, out
    assert "no longer fires" not in out, out
    # calibration: the ignored advisory's entry removed, the same lock is red
    (c / ".cargo" / "audit.toml").write_text("[advisories]\nignore = []\n")
    r = run_script(CURRENT, c, OFFLINE)
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "✗ rust: no known advisory in fuzz/Cargo.lock" in out, out


def test_a_shared_exemption_firing_against_no_governed_lock_is_stale(tmp_path):
    c = _nested(tmp_path, root_ignore='"RUSTSEC-2020-0071",  # time, until 2027')
    (c / "fuzz" / "Cargo.lock").write_text("version = 4\n")
    _git(c, "add", "-A")
    r = subprocess.run(
        ["python3", str(CHECK), "--no-fetch", "--repo", str(c)],
        cwd=c,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 1 and "RUSTSEC-2020-0071 no longer fires" in r.stderr, r.stderr
