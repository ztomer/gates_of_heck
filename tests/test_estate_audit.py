"""A new advisory is reported before a push, not during one (BACKLOG 6.13).

RUSTSEC-2024-0449 turned two repos' pushes red at the same moment with no code change.
scripts/estate_audit.py runs the house audit over every tracked Cargo.lock in the estate on a
schedule; hooks/claude/estate_advisories.py prints what it found at the start of every session,
and says so when the job has stopped. The advisory database here is a fixture: a git repository
holding one planted advisory, read with --no-fetch, so nothing touches the network.
"""

import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
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
        (d / ".gatesrc").write_text(GATED)
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
        ],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(state.read_text())


def _hook(state: Path) -> str:
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input='{"source": "startup"}',
        capture_output=True,
        text=True,
        env={"GOH_ESTATE_STATE": str(state), "PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.fixture
def estate(tmp_path: Path) -> Path:
    root = tmp_path / "Projects"
    _repo(root, "hit", "goh-fixture-crate")
    _repo(root / "group", "clean", "goh-other-crate")
    # A repository inside another one's tree (servers/media_server): its parent does not track
    # its files, so the walk must not stop at the parent.
    _repo(root / "group" / "clean", "nested", "goh-fixture-crate")
    return root


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


def test_first_seen_survives_the_next_run(tmp_path, estate):
    state, db = tmp_path / "state.json", _db(tmp_path, planted=True)
    first = _audit(estate, state, db)["advisories"][PLANTED]["first_seen"]
    data = json.loads(state.read_text())
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(days=5)).isoformat(timespec="seconds")
    data["advisories"][PLANTED]["first_seen"] = old
    state.write_text(json.dumps(data))
    again = _audit(estate, state, db)["advisories"][PLANTED]["first_seen"]
    assert again == old != first
    out = _hook(state)
    assert "1 standing advisory" in out and "NEW" not in out, out


def test_the_first_run_is_a_baseline_not_news(tmp_path, estate):
    state = tmp_path / "state.json"
    found = _audit(estate, state, _db(tmp_path, planted=True))
    assert found["advisories"][PLANTED]["first_seen"] is None, found
    out = _hook(state)
    assert "NEW" not in out and "1 standing advisory(ies) in 2 lockfile(s)" in out, out


def test_a_tree_the_repo_excludes_is_set_aside(tmp_path, estate):
    hit = estate / "hit"
    (hit / "vendor" / "x").mkdir(parents=True)
    (hit / "vendor" / "x" / "Cargo.lock").write_text((hit / "Cargo.lock").read_text())
    (hit / ".gatesrc").write_text(GATED + "export GOH_EXCLUDE='^vendor/'\n")
    _git(hit, "add", "-A")
    _git(hit, "commit", "-qm", "vendored")
    found = _audit(estate, tmp_path / "state.json", _db(tmp_path, planted=True))
    assert found["excluded"] == 1, found
    assert "hit/vendor/x/Cargo.lock" not in found["advisories"][PLANTED]["lockfiles"], found


def test_a_repos_own_exemption_is_honoured(tmp_path, estate):
    (estate / "hit" / ".cargo").mkdir()
    (estate / "hit" / ".cargo" / "audit.toml").write_text(
        f'[advisories]\nignore = ["{PLANTED}"]  # fixture\n'
    )
    state = tmp_path / "state.json"
    found = _audit(estate, state, _db(tmp_path, planted=True))
    assert found["advisories"][PLANTED]["lockfiles"] == ["group/clean/nested/Cargo.lock"], found


def test_a_job_that_never_ran_is_reported(tmp_path):
    assert "never run" in _hook(tmp_path / "absent.json")


def test_a_job_that_stopped_is_reported_as_stale(tmp_path):
    state = tmp_path / "state.json"
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=40)).isoformat(timespec="seconds")
    state.write_text(json.dumps({"ran_at": old, "advisories": {}, "errors": []}))
    assert "STALE" in _hook(state)


def _ungated(root: Path) -> Path:
    """A third-party clone kept for reference or vendoring: a lock with the advisory, and no
    `.gatesrc`, so no house gate has ever run in it."""
    return _repo(root / "references", "vendored-clone", "goh-fixture-crate", gated=False)


def test_an_ungated_repo_is_not_reported_red_and_is_named(tmp_path, estate):
    """No house gate runs in a repo without a `.gatesrc`, so no push of its lockfile is red and
    the session hook must not say it is. It is NAMED, not silently dropped: a house repo that lost
    its `.gatesrc` would otherwise be unaudited and look clean."""
    _ungated(estate)
    state = tmp_path / "state.json"
    found = _audit(estate, state, _db(tmp_path, planted=True))
    assert found["advisories"][PLANTED]["lockfiles"] == [
        "group/clean/nested/Cargo.lock",
        "hit/Cargo.lock",
    ], found
    assert found["lockfiles"] == 3, found
    assert found["ungated"] == ["references/vendored-clone"], found
    out = _hook(state)
    assert "vendored-clone/Cargo.lock" not in out, out


def test_a_repo_that_lost_its_gatesrc_drops_out_visibly(tmp_path, estate):
    """The same repo, gated then not: reported while gated, named as ungated once it is not."""
    state, db = tmp_path / "state.json", _db(tmp_path, planted=True)
    assert "hit/Cargo.lock" in _audit(estate, state, db)["advisories"][PLANTED]["lockfiles"]
    (estate / "hit" / ".gatesrc").unlink()
    found = _audit(estate, state, db)
    assert "hit/Cargo.lock" not in found["advisories"][PLANTED]["lockfiles"], found
    assert found["ungated"] == ["hit"], found


def test_a_registered_repo_outside_the_root_follows_the_same_rule(tmp_path, estate):
    outside = tmp_path / "elsewhere"
    gated = _repo(outside, "gated", "goh-fixture-crate")
    bare = _repo(outside, "bare", "goh-fixture-crate", gated=False)
    state = tmp_path / "state.json"
    (tmp_path / "installed_repos").write_text(f"{gated.resolve()}\n{bare.resolve()}\n")
    found = _audit(estate, state, _db(tmp_path, planted=True))
    assert str(gated.resolve() / "Cargo.lock") in found["advisories"][PLANTED]["lockfiles"], found
    assert str(bare.resolve()) in found["ungated"], found
    assert str(bare.resolve() / "Cargo.lock") not in found["advisories"][PLANTED]["lockfiles"]


def test_the_session_hook_says_how_many_were_set_aside_and_why(tmp_path, estate):
    _ungated(estate)
    state = tmp_path / "state.json"
    _audit(estate, state, _db(tmp_path, planted=False))
    lines = [x for x in _hook(state).splitlines() if "ungated" in x]
    assert len(lines) == 1, lines
    assert "1 repo" in lines[0] and ".gatesrc" in lines[0], lines
    assert "references/vendored-clone" in lines[0], lines


def test_the_session_hook_is_silent_about_ungated_when_there_are_none(tmp_path, estate):
    state = tmp_path / "state.json"
    _audit(estate, state, _db(tmp_path, planted=False))
    assert _hook(state) == ""


def _fixed_since(state: Path, estate: Path, *, new: bool) -> None:
    """The run named the planted crate in hit/Cargo.lock; then the lock moved on (GOH.EA3): a private consumer's
    pulldown-cmark 0.13.4 -> 0.13.6 landed after the daily run, and the hook still said red."""
    if new:
        data = json.loads(state.read_text())
        data["advisories"][PLANTED]["first_seen"] = dt.datetime.now(dt.UTC).isoformat()
        state.write_text(json.dumps(data))
    lock = estate / "hit" / "Cargo.lock"
    lock.write_text(lock.read_text().replace('version = "1.0.0"', 'version = "1.0.1"'))


@pytest.mark.parametrize("new", [True, False], ids=["new", "standing"])
def test_a_lock_fixed_since_the_run_is_not_reported_red(tmp_path, estate, new):
    state = tmp_path / "state.json"
    _audit(estate, state, _db(tmp_path, planted=True))
    _fixed_since(state, estate, new=new)
    out = _hook(state)
    red = [x for x in out.splitlines() if "hit/Cargo.lock" in x and "fixed since the run" not in x]
    assert red == [], out
    assert "fixed since the run" in out and "hit/Cargo.lock" in out, out
    # The lock that still carries the version is still reported.
    assert "NEW" in out if new else "1 standing advisory(ies) in 1 lockfile(s)" in out, out
    if new:
        assert "group/clean/nested/Cargo.lock is red" in out, out


def test_a_lock_that_cannot_be_reread_is_never_reported_fixed(tmp_path, estate):
    state = tmp_path / "state.json"
    _audit(estate, state, _db(tmp_path, planted=True))
    data = json.loads(state.read_text())
    data["advisories"][PLANTED]["first_seen"] = dt.datetime.now(dt.UTC).isoformat()
    state.write_text(json.dumps(data))
    (estate / "hit" / "Cargo.lock").unlink()
    (estate / "group" / "clean" / "nested" / "Cargo.lock").write_text("[[package\nnot toml")
    out = _hook(state)
    assert "fixed since the run" not in out, out
    assert "hit/Cargo.lock" in out and "group/clean/nested/Cargo.lock" in out, out
