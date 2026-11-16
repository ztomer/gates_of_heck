"""`goh dead-port`: a test's dead endpoint on a literal ephemeral port, a structural step in every
repo (GOH.DEADPORTSCAN, `crates/goh/src/deadport.rs`; the class is EST.DEADPORT).

ztools' `call_osaurus` test expected a refused connect from a literal loopback port (59999) in the
OS's ephemeral range, so any outgoing connection could be handed it and the test failed at random
(it refused ztools' v0.124.0 pin push, 2026-11-15). The shape table is `deadport/tests.rs`; this
is the whole-repo behaviour: a consumer's structural gate goes red on a planted site for its named
reason, a service port in non-test code and a socket-read port stay green, the grace keeps it quiet
until `deadport::OVER_FROM`, a tracked seed entry warns, the escape needs a reason.

Every fixture builds its port from `DEAD`: this file is a test the scanner reads.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, hermetic_env, run_goh, write

LABEL = "no test endpoint on a literal ephemeral port"
DEAD = 59_999
# The incident's shape: a test that expects the connect to a "dead" literal port to fail.
PLANTED = (
    "from client import call\n\n\ndef test_refused():\n"
    f'    r = call("http://127.0.0.1:{DEAD}/v1")\n'
    "    assert r.error\n"
)
# The fix's shape: the port is read from a socket the test holds.
HELD = (
    "from client import call\nfrom dead import hold\n\n\ndef test_refused():\n"
    "    with hold() as port:\n"
    '        r = call(f"http://127.0.0.1:{port}/v1")\n'
    "    assert r.error\n"
)
# A service's own port, in non-test code: never read.
SERVICE = f'PORT = 0\nBASE = "http://127.0.0.1:{DEAD}"\n'
ESCAPED = PLANTED.replace('/v1")', '/v1")  # dead-port-ok: the fixture server listens here')


def _env(tmp_path: Path, grace: str = "over") -> dict[str, str]:
    return hermetic_env(GOH_DIR=str(REPO_ROOT), GOH_GRACE=grace, GOH_SEED_DIR=str(tmp_path / "s"))


def _check(repo: Path, tmp_path: Path, grace: str = "over") -> subprocess.CompletedProcess:
    return run_goh(repo, "dead-port", env=_env(tmp_path, grace))


def _structural(repo: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return run_goh(repo, "structural", *args, env=_env(tmp_path))


def test_a_consumer_structural_gate_is_red_on_a_planted_site_for_its_reason(
    repo: Path, tmp_path: Path
) -> None:
    write(repo, "tests/test_client.py", PLANTED)
    write(repo, "src/server.py", SERVICE)
    commit_all(repo)
    r = _structural(repo, tmp_path, "--full", "--keep-going")
    assert r.returncode == 1 and f"structural: {LABEL}" in r.stderr, r.stdout + r.stderr
    assert "tests/test_client.py:5:" in r.stderr, r.stderr
    assert f"port {DEAD} is in the ephemeral range" in r.stderr, r.stderr
    assert "WITHOUT SO_REUSEADDR" in r.stderr, "the message names the fix"
    assert "src/server.py" not in r.stderr, "a service port in non-test code is not judged"
    write(repo, "tests/test_client.py", HELD)
    commit_all(repo)
    r = _structural(repo, tmp_path, "--full", "--keep-going")
    assert f"structural: {LABEL}" not in r.stderr, r.stderr


def test_the_staged_scope_refuses_the_commit_that_brings_one(repo: Path, tmp_path: Path) -> None:
    write(repo, "tests/test_client.py", PLANTED)
    git(repo, "add", "-A")
    r = _structural(repo, tmp_path, "--staged")
    assert r.returncode == 1 and f"structural: {LABEL}" in r.stderr, r.stdout + r.stderr
    assert "tests/test_client.py:5:" in r.stderr, r.stderr


def test_within_the_grace_it_reads_and_judges_nothing(repo: Path, tmp_path: Path) -> None:
    write(repo, "tests/test_client.py", PLANTED)
    commit_all(repo)
    assert _check(repo, tmp_path).returncode == 1, "calibration: the site is red once judged"
    r = _check(repo, tmp_path, grace="within")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_rust_cfg_test_region_is_judged_and_the_service_above_it_is_not(
    repo: Path, tmp_path: Path
) -> None:
    write(
        repo,
        "src/lib.rs",
        f'pub const BASE: &str = "http://127.0.0.1:{DEAD}";\n\n'
        "#[cfg(test)]\nmod tests {\n    #[test]\n    fn refused() {\n"
        f'        let _ = ("127.0.0.1", {DEAD});\n'
        "    }\n}\n",
    )
    commit_all(repo)
    r = _check(repo, tmp_path)
    assert r.returncode == 1 and "src/lib.rs:7:" in r.stderr, r.stdout + r.stderr
    assert "src/lib.rs:1:" not in r.stderr, r.stderr


def test_the_escape_needs_a_reason(repo: Path, tmp_path: Path) -> None:
    write(repo, "tests/test_client.py", ESCAPED)
    commit_all(repo)
    assert _check(repo, tmp_path).returncode == 0
    write(repo, "tests/test_client.py", ESCAPED.split("dead-port-ok:")[0] + "dead-port-ok:\n")
    commit_all(repo)
    r = _check(repo, tmp_path)
    assert r.returncode == 1 and "names no reason" in r.stderr, r.stdout + r.stderr


def test_a_tracked_seed_entry_warns_and_over_its_count_is_red(repo: Path, tmp_path: Path) -> None:
    write(repo, "tests/test_client.py", PLANTED)
    commit_all(repo)
    root = git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    entry = {"root": root, "repo": "fixture", "path": "tests/test_client.py", "count": 1}
    seed = {"why": "a test's", "entries": [{**entry, "reason": "unreviewed: a test"}]}
    write(repo, "dead_port_seed.json", json.dumps(seed))
    commit_all(repo)
    r = _check(repo, tmp_path)
    assert r.returncode == 0 and "tests/test_client.py" in r.stderr, r.stdout + r.stderr
    write(
        repo, "tests/test_client.py", PLANTED + PLANTED.split("\n\n\n", 1)[1].replace("d(", "d2(")
    )
    commit_all(repo)
    assert _check(repo, tmp_path).returncode == 1, "over its count is red"


def test_the_dispatcher_knows_the_check(repo: Path, tmp_path: Path) -> None:
    write(repo, "tests/test_client.py", PLANTED)
    commit_all(repo)
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "goh.sh"), "dead-port"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 1 and "tests/test_client.py:5:" in r.stderr, r.stdout + r.stderr


def test_this_repo_has_no_site(tmp_path: Path) -> None:
    r = _check(REPO_ROOT, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
