"""`goh remote-shard`'s lane-facing half (GOH.REMOTEEVICT, GOH.REMOTEWIRE; split from
tests/test_remote_shard.py for its line cap): the worker's evictor after every run, sccache in the
image, one tree per lane slot, `--no-local`, the recorded Mac skip set, an unparsable junit."""

from __future__ import annotations

import re

from source_closure import copy_closure
from test_remote_shard import DOCKERFILE, TOOL, _git, _log, _run, _slot_tree, rig

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
    assert "sccache --version; } > /opt/goh-worker-versions.txt" in text


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
