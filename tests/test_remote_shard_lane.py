"""`goh remote-shard`'s lane-facing half (GOH.REMOTEEVICT, GOH.REMOTEWIRE; split from
tests/test_remote_shard.py for its line cap): the worker's evictor after every run, sccache in the
image, one tree per lane slot, `--no-local`, the recorded Mac skip set, an unparsable junit, the
tags and the advisory database that travel to the worker (GOH.WORKERFAILS)."""

from __future__ import annotations

import os
import re

from source_closure import copy_closure
from test_remote_shard import DOCKERFILE, TOOL, _git, _log, _run, _slot_tree, rig

TOOL_UID = "1000:1000"  # tools/remote_shard.py `conf`'s GOH_REMOTE_USER default

__all__ = ["rig"]  # the fixture, shared with tests/test_remote_shard.py


def test_every_run_ends_with_the_workers_evictor_under_the_workers_rules(rig):
    """GOH.REMOTEEVICT: the evictor runs in the container after the shard, at the ceiling and the
    floor the probe enforces, with what the run used stamped first; it reports, never fails."""
    copy_closure(rig["tree"], "lib/worker_evict.py")
    _git(rig["tree"], "add", "-A")
    _git(rig["tree"], "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "evictor")
    r = _run(rig, "tests/test_x.py", GOH_REMOTE_CEILING_GB="80", GOH_REMOTE_FREE_FLOOR_GB="0")
    assert r.returncode == 0, r.stdout + r.stderr
    line = next(ln for ln in _log(rig).splitlines() if "worker_evict.py run" in ln)
    assert "--root /work --ceiling-gib 80 --floor-gib 0" in line, line
    t = f"/work/trees/{_slot_tree(rig).name}"
    assert f"touch {t}/target/.goh-used /work/cache/home/.cargo/.goh-used" in line, line
    assert "worker cache:" in r.stderr, r.stderr
    assert (rig["remote"] / "cache" / "evict.json").is_file()


def test_a_failing_evictor_never_changes_the_runs_verdict(rig):
    r = _run(rig, "tests/test_x.py")  # the rig's tree carries no lib/worker_evict.py
    assert r.returncode == 0, r.stdout + r.stderr
    assert "worker evictor failed (the run stands)" in r.stderr
    assert "3 passed" in r.stdout


def test_the_image_carries_sccache_behind_goh_s_wrapper_and_records_its_version():
    """GOH.REMOTEEVICT: cargo on the worker reuses compiled crates; the version is in the parity
    record, so a run on an image without it is visible."""
    text = DOCKERFILE.read_text()
    assert re.search(r"^ARG SCCACHE_VERSION=\d+\.\d+\.\d+$", text, re.MULTILINE)
    assert "install -m 0755" in text and "/usr/local/bin/sccache" in text
    # cargo's wrapper is the slot tree's lib/goh-rustc, set on each run's `docker exec`
    assert "RUSTC_WRAPPER={t}/lib/goh-rustc" in TOOL.read_text()
    assert re.search(r"SCCACHE_CACHE_SIZE=\d+G", text)
    assert re.search(r"sccache --version;[^\n]*\} > /opt/goh-worker-versions.txt", text)


def test_each_slot_has_its_own_tree_and_lock_so_two_lands_never_queue_on_one(rig):
    """GOH.REMOTEWIRE: one remote tree per lane slot replaced the one lock over one tree."""
    for slot in ("lane1", "lane2"):
        r = _run(rig, "--slot", slot, "tests/test_x.py")
        assert r.returncode == 0, r.stdout + r.stderr
        assert (rig["remote"] / "trees" / slot / "tests" / "test_x.py").is_file()
        assert f"flock {rig['remote']}/out/run-{slot}.lock" in _log(rig)
    assert "run.lock" not in _log(rig)
    assert _run(rig, "--slot", "../x", "tests/test_x.py").returncode == 2


def test_no_local_hands_a_fallback_back_to_the_caller_with_exit_3(rig):
    r = _run(rig, "--no-local", "tests/test_x.py", FAKE_SSH_DOWN="1")
    assert r.returncode == 3, r.stdout + r.stderr
    assert (
        "unreachable" in r.stdout + r.stderr and "the caller runs the shard" in r.stdout + r.stderr
    )


def test_the_recorded_mac_skip_set_is_the_parity_reference(rig):
    """The rig's remote junit skips test_d: a recorded set naming it agrees, one without differs."""
    skips = rig["tmp"] / "mac_skips.txt"
    skips.write_text("tests.test_x::test_d\n")
    assert _run(rig, "--no-local", "--mac-skips", str(skips), "tests/test_x.py").returncode == 0
    skips.write_text("")
    r = _run(rig, "--no-local", "--mac-skips", str(skips), "tests/test_x.py")
    assert r.returncode == 3 and "tests.test_x::test_d" in r.stdout + r.stderr


def test_the_recorded_skip_set_is_exact_not_a_count(rig):
    """GOH.REMOTESHARE: one recorded skip the worker ran and one it skipped the Mac does not
    record are equal COUNTS, never the same verdict: both are named and the share goes back."""
    skips = rig["tmp"] / "mac_skips.txt"
    skips.write_text("tests.test_x::test_a\n")
    r = _run(rig, "--no-local", "--mac-skips", str(skips), "tests/test_x.py")
    said = r.stdout + r.stderr
    assert r.returncode == 3 and "tests.test_x::test_a" in said and "tests.test_x::test_d" in said


def test_a_remote_junit_that_does_not_parse_is_a_fallback_not_a_crash(rig):
    (rig["tmp"] / "junit.xml").write_text("<testsuites><testsuite")
    r = _run(rig, "--no-local", "tests/test_x.py")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "does not parse" in r.stdout + r.stderr and "Traceback" not in r.stderr


def test_the_image_carries_the_tools_and_the_user_the_suite_meets():
    """GOH.WORKERFAILS: 172 failed and 74 errors in the worker's full suite (2026-11-22) were
    largely the image: no git-cliff (tools/pytest.sh, goh ship), no cargo-machete (the rust gate),
    no node (check_js_syntax), and no passwd entry for the run's uid (`Path.home()` with HOME
    dropped raised, pytest's basetemp said `pytest-of-unknown`). Each at the Mac's release, its
    version in the parity record."""
    text = DOCKERFILE.read_text()
    for tool in ("git-cliff", "cargo-machete", "node"):
        assert f"/usr/local/bin/{tool}" in text, tool
    assert f'e("GOH_REMOTE_USER", "{TOOL_UID}")' in TOOL.read_text()
    uid, gid = TOOL_UID.split(":")
    assert f"groupadd -g {gid} " in text and f"useradd -u {uid} -g {gid} " in text
    versions = re.search(r"\{ python3 -V;.*\} > /opt/goh-worker-versions.txt", text)
    assert versions and all(t in versions.group(0) for t in ("git-cliff", "cargo-machete", "node"))


def test_the_tags_travel_and_a_tree_synced_without_them_gets_them_whole(rig):
    """Tests read the checkout's release tags (`every tag has its changelog heading`,
    tests/test_self_walk.py), and the bundle carried HEAD only, so the worker's tree had none
    (GOH.WORKERFAILS). Tags travel with their commits, and a tree whose tags are not the Mac's (one
    synced before they travelled) is sent a whole bundle once."""
    tree = rig["tree"]
    _git(tree, "tag", "-a", "v0.1.0", "-m", "one")
    assert _run(rig, "tests/test_x.py").returncode == 0
    remote = _slot_tree(rig)
    assert _git(remote, "tag").stdout.split() == ["v0.1.0"]
    (tree / "tests" / "test_y.py").write_text("def test_y(): pass\n")
    _git(tree, "add", "-A")
    _git(tree, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "two")
    _git(tree, "tag", "v0.2.0")
    assert _run(rig, "tests/test_x.py").returncode == 0
    assert _git(remote, "tag").stdout.split() == ["v0.1.0", "v0.2.0"]
    for t in ("v0.1.0", "v0.2.0"):
        _git(remote, "tag", "-d", t)
    assert _run(rig, "tests/test_x.py").returncode == 0
    assert _git(remote, "tag").stdout.split() == ["v0.1.0", "v0.2.0"]
    assert _git(remote, "rev-parse", "HEAD").stdout == _git(tree, "rev-parse", "HEAD").stdout


def test_a_run_of_the_rig_fetches_its_advisories_from_the_run_never_the_network(rig):
    """GOH.FSEVQUIET: the fake docker runs the clone for real, so a rig that names no upstream
    cloned RustSec's 1,883 files over the network into every test's remote (3,850 file events a
    test, 27% of a pre-step's under the pytest trees, 2026-11-25)."""
    assert _run(rig, "tests/test_x.py").returncode == 0
    rows = _log(rig).splitlines()
    fetch = [ln for ln in rows if ln.startswith("$ docker run --rm") and "-advisories" in ln]
    assert len(fetch) == 1 and f"file://{rig['advisories']}" in fetch[0], fetch
    assert len(list(rig["remote"].rglob("*"))) < 200


def test_the_advisory_database_is_fetched_once_a_day_by_a_networked_container(rig):
    """The worker has no network, and the rust gate's audits the suite drives read a cached
    advisory database (GOH_DEPS_OFFLINE=1: `--no-fetch`, seeded from ~/.cargo/advisory-db): with
    none, 9 rust-gate tests failed "couldn't fetch advisory database" (GOH.WORKERFAILS). So a
    networked short-lived container clones it into the worker's HOME, again when it is a day old."""
    assert _run(rig, "tests/test_x.py").returncode == 0
    db = rig["remote"] / "cache" / "home" / ".cargo" / "advisory-db"
    assert (db / "README.md").read_text() == "advisories\n"

    def fetches() -> int:
        rows = _log(rig).splitlines()
        return sum(ln.startswith("$ docker run --rm") and "-advisories" in ln for ln in rows)

    assert fetches() == 1
    assert _run(rig, "tests/test_x.py").returncode == 0
    assert fetches() == 1, "a fresh copy was fetched again"
    st = db.stat()
    os.utime(db, (st.st_atime - 2 * 86400, st.st_mtime - 2 * 86400))
    assert _run(rig, "tests/test_x.py").returncode == 0
    assert fetches() == 2, "a two-day-old copy was not refreshed"
