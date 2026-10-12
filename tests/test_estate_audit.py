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


def _git(d: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=d,
        capture_output=True,
        check=True,
    )


def _repo(root: Path, name: str, crate: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    fast_init(d)
    (d / "Cargo.lock").write_text(LOCK.format(name=crate, zeros="0" * 64))
    _git(d, "add", "Cargo.lock")
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
    (hit / ".gatesrc").write_text("export GOH_EXCLUDE='^vendor/'\n")
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
