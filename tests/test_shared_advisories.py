"""One estate ruling for an advisory every consumer inherits through one upstream
(GOH.SHAREDADVISORY).

RUSTSEC-2026-0543 (chrono unmaintained, no patch) fires in every lock that carries rmcp, which
needs chrono non-optionally. Nine repos hand-adding the same ignore is nine copies of one ruling
nobody re-checks together. goh vends ONE file, gates/shared_advisories.toml; rust_gate.sh's audit
step passes `--ignore ID` for an entry only in a lock where the blocking crate still reaches the
advised one, and checks/check_audit_ignores.py --shared judges an entry stale when it fires in no
estate lock. The ban recipe (cargo-deny `[bans]` with `wrappers`) keeps a NEW direct use red.

Locks are hand-written; cargo-audit reads names and versions only, --no-fetch.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_rust_audit import ANSI_TERM, OFFLINE, UNMAINTAINED, _crate
from test_rust_gate import CURRENT, _git, run_script

sys.path.insert(0, str(REPO_ROOT / "lib"))
import shared_advisories as sa

CHECK = REPO_ROOT / "checks" / "check_audit_ignores.py"
BLOCKER = '\n[[package]]\nname = "goh-blocker"\nversion = "1.0.0"\n{deps}'
SHARED = f"""[[advisory]]
id = "{UNMAINTAINED}"
crate = "ansi_term"
blocker = "goh-blocker"
reason = "a fixture: goh-blocker needs ansi_term and has no release without it"
reopen = "goh-blocker no longer requires ansi_term"
"""


def _lock(*, edge: bool) -> str:
    deps = 'dependencies = [\n "ansi_term",\n]\n' if edge else ""
    return "version = 4\n" + BLOCKER.format(deps=deps) + ANSI_TERM


def _shared(tmp: Path, text: str = SHARED) -> Path:
    f = tmp / "shared.toml"
    f.write_text(text)
    return f


# ── the vended file ─────────────────────────────────────────────────────────────────────────


def test_the_vended_file_carries_the_chrono_ruling_with_its_reopen_condition():
    (entry,) = [e for e in sa.load() if e.id == "RUSTSEC-2026-0543"]
    assert (entry.crate, entry.blocker) == ("chrono", "rmcp")
    assert entry.reason and "no longer requires" in entry.reopen


def test_an_entry_missing_a_field_is_refused(tmp_path):
    with pytest.raises(ValueError, match="reopen"):
        sa.load(_shared(tmp_path, SHARED.replace("reopen", "#")))


def test_it_applies_only_where_the_blocker_reaches_the_crate():
    e = sa.load_text(SHARED)[0]
    assert sa.applies(e, _lock(edge=True))
    # the blocker dropped it: the shared exemption no longer covers this lock
    assert not sa.applies(e, _lock(edge=False))
    assert sa.dropped(e, _lock(edge=False)) and not sa.dropped(e, _lock(edge=True))


# ── the gate ────────────────────────────────────────────────────────────────────────────────


def _repo(tmp: Path, *, edge: bool) -> Path:
    c = _crate(tmp / "repo", unmaintained=False)
    (c / "Cargo.lock").write_text(_lock(edge=edge))
    _git(c, "add", "-A")
    return c


needs_audit = pytest.mark.skipif(shutil.which("cargo-audit") is None, reason="no cargo-audit")


@needs_audit
def test_a_shared_entry_makes_the_audit_green_with_no_repo_edit(tmp_path):
    c = _repo(tmp_path, edge=True)
    env = {**OFFLINE, "GOH_SHARED_ADVISORIES": str(_shared(tmp_path))}
    r = run_script(CURRENT, c, env)
    out = r.stdout + r.stderr
    assert "✓ no known advisory in Cargo.lock" in out, out
    # calibration: no shared file, the same lock is red
    r = run_script(CURRENT, c, {**env, "GOH_SHARED_ADVISORIES": str(_shared(tmp_path, ""))})
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "✗ rust: no known advisory in Cargo.lock" in out, out


@needs_audit
def test_a_blocker_that_dropped_the_crate_reopens_the_ruling(tmp_path):
    c = _repo(tmp_path, edge=False)
    r = run_script(CURRENT, c, {**OFFLINE, "GOH_SHARED_ADVISORIES": str(_shared(tmp_path))})
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert f"{UNMAINTAINED}: goh-blocker no longer depends on ansi_term" in out, out


# ── the estate ratchet ──────────────────────────────────────────────────────────────────────


def _estate(tmp: Path, *, edge: bool, carries: bool = True) -> subprocess.CompletedProcess:
    c = _repo(tmp, edge=edge)
    if not carries:
        (c / "Cargo.lock").write_text("version = 4\n")
        _git(c, "add", "-A")
    return subprocess.run(
        [sys.executable, str(CHECK), "--no-fetch", "--shared", "--estate", str(tmp / "repo")],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
        env={**__import__("os").environ, "GOH_SHARED_ADVISORIES": str(_shared(tmp))},
    )


@needs_audit
def test_a_shared_entry_firing_in_some_estate_lock_stands(tmp_path):
    r = _estate(tmp_path, edge=True)
    assert r.returncode == 0, r.stdout + r.stderr


@needs_audit
def test_a_shared_entry_the_estate_no_longer_triggers_is_stale(tmp_path):
    r = _estate(tmp_path, edge=True, carries=False)
    assert r.returncode == 1 and f"{UNMAINTAINED} fires in no estate lock" in r.stderr, r.stderr


# ── the ban recipe keeps a new direct use red ───────────────────────────────────────────────

DENY = '[bans]\ndeny = [{ crate = "chrono", wrappers = ["rmcp", "schemars"] }]\n'


def _pkg(d: Path, name: str, deps: str = "") -> None:
    (d / name / "src").mkdir(parents=True)
    (d / name / "Cargo.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2024"\n'
        f'license = "MIT"\npublish = false\n\n[dependencies]\n{deps}'
    )
    (d / name / "src" / "lib.rs").write_text("")


def _ban(tmp: Path, *, direct: bool) -> subprocess.CompletedProcess:
    _pkg(tmp, "chrono")
    _pkg(tmp, "rmcp", 'chrono = { path = "../chrono" }\n')
    extra = 'chrono = { path = "../chrono" }\n' if direct else ""
    _pkg(tmp, "app", f'rmcp = {{ path = "../rmcp" }}\n{extra}')
    (tmp / "app" / "deny.toml").write_text(DENY)
    return subprocess.run(
        ["cargo", "deny", "--offline", "check", "bans"],
        cwd=tmp / "app",
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


@pytest.mark.skipif(shutil.which("cargo-deny") is None, reason="no cargo-deny")
@pytest.mark.parametrize("direct", [False, True])
def test_the_ban_recipe_lets_the_wrapper_through_and_stops_a_direct_use(tmp_path, direct):
    r = _ban(tmp_path, direct=direct)
    assert (r.returncode != 0) is direct, r.stderr
