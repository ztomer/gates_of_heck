"""A new advisory is reported before a push, not during one (BACKLOG 6.13).

RUSTSEC-2024-0449 turned two repos' pushes red at the same moment with no code change.
scripts/estate_audit.py runs the house audit over every tracked Cargo.lock in the estate on a
schedule; hooks/claude/estate_advisories.py prints what it found at the start of every session,
and says so when the job has stopped. The advisory database here is a fixture: a git repository
holding one planted advisory, read with --no-fetch, so nothing touches the network.
"""

import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from _gatesrc_kit import write_gatesrc
from _small_store import estate_state, put_estate_state
from _tree_clone import from_template
from conftest import REPO_ROOT

pytestmark = pytest.mark.skipif(
    shutil.which("cargo-audit") is None, reason="cargo-audit not installed"
)

SCRIPT = REPO_ROOT / "scripts" / "estate_audit.py"
HOOK = REPO_ROOT / "hooks" / "claude" / "estate_advisories.py"
PLANTED = "RUSTSEC-2099-0001"
ADVISORY = f"""```toml
[advisory]
id = "{PLANTED}"
package = "goh-fixture-crate"
date = "2099-01-01"
url = "https://example.invalid/advisory"
categories = ["memory-corruption"]

[versions]
patched = [">= 1.0.1"]
```

# A planted advisory

Written by tests/test_estate_audit.py.
"""
LOCK = """version = 4

[[package]]
name = "{name}"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "{zeros}"
"""


GATED = """# the house gate runs here
GOH_CI_STEPS='"$GOH_DIR/gates/rust_gate.sh"'
"""


def _git(d: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=d,
        capture_output=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )


def _repo(root: Path, name: str, crate: str, *, gated: bool = True) -> Path:
    """A repo with one tracked lock. `gated` writes the `.gatesrc` install.sh writes into every
    house repo: it is what says the house gate runs there, and the estate audit reads it."""
    d = root / name
    d.mkdir(parents=True)
    fast_init(d)
    (d / "Cargo.lock").write_text(LOCK.format(name=crate, zeros="0" * 64))
    if gated:
        # The house gate runs here, and its push audits the root lock (rust_gate.sh, no args): the
        # estate audit reads that from the gate, so a fixture whose gate audits nothing would be
        # "not audited at push", not red (tests/test_estate_gate_coverage.py).
        write_gatesrc(d, GATED)
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "lock")
    return d


def _db(tmp: Path, *, planted: bool) -> Path:
    db = tmp / "db"
    (db / "crates" / "goh-fixture-crate").mkdir(parents=True)
    fast_init(db)
    (db / "README.md").write_text("fixture\n")
    if planted:
        (db / "crates" / "goh-fixture-crate" / f"{PLANTED}.md").write_text(ADVISORY)
    _git(db, "add", "-A")
    _git(db, "commit", "-qm", "db")
    return db


def _plant(db: Path) -> None:
    (db / "crates" / "goh-fixture-crate" / f"{PLANTED}.md").write_text(ADVISORY)
    _git(db, "add", "-A")
    _git(db, "commit", "-qm", "advisory")


def _audit(root: Path, state: Path, db: Path) -> dict:
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(root),
            "--state",
            str(state),
            "--db",
            str(db),
            "--no-fetch",
            "--no-grace-watch",
        ],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return estate_state(state)


def _hook(state: Path) -> str:
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input='{"source": "startup"}',
        capture_output=True,
        text=True,
        env={
            "GOH_ESTATE_STATE": str(state),
            "GOH_REUSE_DIR": str(state.parent / "reuse"),
            "PATH": "/usr/bin:/bin",
        },
        check=False,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.fixture
def estate(tmp_path: Path, tmp_path_factory) -> Path:
    """Three repositories: built once per worker, cloned per test (GOH.FIXTURECOST)."""
    return from_template(tmp_path_factory, "estate-audit", _build_estate, tmp_path / "Projects")


def _build_estate(root: Path) -> None:
    _repo(root, "hit", "goh-fixture-crate")
    _repo(root / "group", "clean", "goh-other-crate")
    # A repository inside another one's tree (a nested server repo): its parent does not track
    # its files, so the walk must not stop at the parent.
    _repo(root / "group" / "clean", "nested", "goh-fixture-crate")


def test_a_new_advisory_is_reported_by_the_next_run_and_at_session_start(tmp_path, estate):
    state, db = tmp_path / "state.json", _db(tmp_path, planted=False)
    assert _audit(estate, state, db)["advisories"] == {}
    assert _hook(state) == ""
    _plant(db)
    found = _audit(estate, state, db)
    assert list(found["advisories"]) == [PLANTED], found
    assert found["advisories"][PLANTED]["lockfiles"] == [
        "group/clean/nested/Cargo.lock",
        "hit/Cargo.lock",
    ], found
    assert found["lockfiles"] == 3, found
    out = _hook(state)
    assert f"NEW {PLANTED}" in out and "hit/Cargo.lock" in out, out


def test_a_job_that_never_ran_is_reported(tmp_path):
    assert "never run" in _hook(tmp_path / "absent.json")


def test_a_job_that_stopped_is_reported_as_stale(tmp_path):
    state = tmp_path / "state.json"
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=40)).isoformat(timespec="seconds")
    put_estate_state(state, {"ran_at": old, "advisories": {}, "errors": []})
    assert "STALE" in _hook(state)


def _ungated(root: Path) -> Path:
    """A third-party clone kept for reference or vendoring: a lock with the advisory, and no
    `.gatesrc`, so no house gate has ever run in it."""
    return _repo(root / "references", "vendored-clone", "goh-fixture-crate", gated=False)


NESTED, HIT = "group/clean/nested/Cargo.lock", "hit/Cargo.lock"


def test_what_a_run_reads_and_what_it_sets_aside(tmp_path, estate):
    """One estate holding a repo for each rule, one run and the next (GOH.PYSLOW2: nine runs):
    * a tree the repo's GOH_EXCLUDE names is set aside, and counted;
    * a repo with no `.gatesrc` is not red -- no house gate runs there, so no push of its lockfile
      is -- and is NAMED, not silently dropped: a house repo that lost its `.gatesrc` would
      otherwise be unaudited and look clean;
    * a registered repo outside the root follows the same rule;
    * the first run is a baseline, not news; `first_seen` survives the next run;
    * the next run: `hit` lost its .gatesrc, and `gated` has an exemption of its own."""
    hit, outside = estate / "hit", tmp_path / "elsewhere"
    (hit / "vendor" / "x").mkdir(parents=True)
    (hit / "vendor" / "x" / "Cargo.lock").write_text((hit / "Cargo.lock").read_text())
    write_gatesrc(hit, GATED + "export GOH_EXCLUDE='^vendor/'\n")
    _git(hit, "add", "-A")
    _git(hit, "commit", "-qm", "vendored")
    _ungated(estate)
    gated = _repo(outside, "gated", "goh-fixture-crate").resolve()
    bare = _repo(outside, "bare", "goh-fixture-crate", gated=False).resolve()
    state, db = tmp_path / "state.json", _db(tmp_path, planted=True)
    (tmp_path / "installed_repos").write_text(f"{gated}\n{bare}\n")

    found = _audit(estate, state, db)
    assert list(found["advisories"]) == [PLANTED], found
    locks = found["advisories"][PLANTED]["lockfiles"]
    assert sorted(locks) == sorted([NESTED, HIT, str(gated / "Cargo.lock")]), found
    assert (found["lockfiles"], found["excluded"]) == (4, 1), found
    assert sorted(found["ungated"]) == sorted(["references/vendored-clone", str(bare)]), found
    assert found["advisories"][PLANTED]["first_seen"] is None, found
    out = _hook(state)
    assert "NEW" not in out and "1 standing advisory in 3 lockfiles" in out, out
    assert "vendored-clone/Cargo.lock" not in out, out
    lines = [x for x in out.splitlines() if "ungated" in x]
    assert len(lines) == 1 and "2 repos" in lines[0] and ".gatesrc" in lines[0], lines
    assert "references/vendored-clone" in lines[0], lines

    old = (dt.datetime.now(dt.UTC) - dt.timedelta(days=5)).isoformat(timespec="seconds")
    data = estate_state(state)
    data["advisories"][PLANTED]["first_seen"] = old
    put_estate_state(state, data)
    (hit / ".gatesrc").unlink()
    (gated / ".cargo").mkdir()
    (gated / ".cargo" / "audit.toml").write_text(
        f'[advisories]\nignore = ["{PLANTED}"]  # fixture\n'
    )
    found = _audit(estate, state, db)
    assert found["advisories"][PLANTED]["first_seen"] == old, found
    assert found["advisories"][PLANTED]["lockfiles"] == [NESTED], found
    assert "hit" in found["ungated"], found
    out = _hook(state)
    assert "1 standing advisory" in out and "NEW" not in out, out


def test_a_lock_fixed_since_the_run_is_not_reported_red(tmp_path, estate):
    """The run named the planted crate in hit/Cargo.lock; then the lock moved on (GOH.EA3): a
    private consumer's pulldown-cmark 0.13.4 -> 0.13.6 landed after the daily run, and the hook
    still said red. So for a standing advisory and a new one; a lock that cannot be reread is never
    fixed."""
    state = tmp_path / "state.json"
    data = _audit(estate, state, _db(tmp_path, planted=True))
    lock, nested = estate / "hit" / "Cargo.lock", estate / NESTED
    lock.write_text(lock.read_text().replace('version = "1.0.0"', 'version = "1.0.1"'))
    for new in (False, True):
        if new:
            data["advisories"][PLANTED]["first_seen"] = dt.datetime.now(dt.UTC).isoformat()
            put_estate_state(state, data)
        out = _hook(state)
        red = [x for x in out.splitlines() if HIT in x and "fixed since the run" not in x]
        assert red == [], out
        assert "fixed since the run" in out and HIT in out, out
        # The lock that still carries the version is still reported.
        assert "NEW" in out if new else "1 standing advisory in 1 lockfile" in out, out
        if new:
            assert f"{NESTED} is red" in out, out
    lock.unlink()
    nested.write_text("[[package\nnot toml")
    out = _hook(state)
    assert "fixed since the run" not in out, out
    assert HIT in out and NESTED in out, out
