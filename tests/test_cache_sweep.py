"""The evictor's sweep acts on real directories: demote with a large tier, delete without one, never
touch a held build dir (GOH.CACHEEVICT). Tmp roots, a tmp "drive", fake sizes and free space;
nothing touches ~/.cargo/build, ~/.cache/goh, /Volumes or a live sccache.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import cache_evict as ce
import cache_move
import cache_sweep
import cache_tier

GIB = 1 << 30
NOW = 1_800_000_000.0


def _bd(root, name, age_h):
    d = root / "build" / name[:2] / name
    (d / "debug" / "deps").mkdir(parents=True)
    (d / "debug" / "deps" / "liba-0.rlib").write_bytes(b"r" * 100)
    (d / "debug" / ".cargo-lock").write_text("")
    t = NOW - age_h * 3600
    for p in [d / "debug" / "deps", d / "debug" / ".cargo-lock", d / "debug", d]:
        os.utime(p, (t, t))
    return d


def _roots(tmp_path, free_gib=400.0):
    (tmp_path / "cov").mkdir()
    (tmp_path / "sccache").mkdir()
    return cache_sweep.Roots(str(tmp_path / "build"), str(tmp_path / "cov"),
                             str(tmp_path / "sccache"), str(tmp_path), 30.0,
                             size=lambda p: 0 if p.endswith("sccache") else 40 * GIB,
                             free=lambda p: int(free_gib * GIB))  # fmt: skip


def _drive(tmp_path):
    root = tmp_path / "drive" / "cache.noindex"
    root.mkdir(parents=True)
    v = cache_tier.Verdict(str(root), True, "test drive", 900 * GIB, 1000 * GIB)
    opener = lambda v: os.open(v.root, os.O_RDONLY | os.O_DIRECTORY)  # noqa: E731
    return root, v, opener


def test_with_the_large_tier_cold_dirs_demote_and_the_hot_one_stays(tmp_path):
    cold, hot = _bd(tmp_path, "aa11", 72), _bd(tmp_path, "bb22", 2)
    root, v, opener = _drive(tmp_path)
    state = str(tmp_path / "state.json")
    report = cache_sweep.sweep(_roots(tmp_path), v, state, NOW, True, opener)
    assert [(d["verb"], d["key"], d["ok"]) for d in report["done"]] == [("demote", str(cold), True)]
    assert hot.exists() and not cold.exists()
    assert (root / "demoted" / "build" / cache_move.slot(str(cold)) / "debug").is_dir()
    assert ce.load(state)["L"] > 0
    again = cache_sweep.sweep(_roots_again(tmp_path), v, state, NOW, False, opener)
    keys = {(o["key"], o["verb"]) for o in again["actions"]}
    assert (str(cold), "demote") not in keys  # the copy is external now, counted there


def _roots_again(tmp_path):
    return cache_sweep.Roots(str(tmp_path / "build"), str(tmp_path / "cov"),
                             str(tmp_path / "sccache"), str(tmp_path), 30.0,
                             size=lambda p: 40 * GIB, free=lambda p: 400 * GIB)  # fmt: skip


def test_without_the_large_tier_the_cold_dir_is_deleted(tmp_path):
    cold, hot = _bd(tmp_path, "aa11", 72), _bd(tmp_path, "bb22", 2)
    v = cache_tier.Verdict(str(tmp_path / "int"), False, "absent")
    report = cache_sweep.sweep(_roots(tmp_path, free_gib=10), v, str(tmp_path / "s.json"), NOW,
                               True)  # fmt: skip
    assert [d["verb"] for d in report["done"]] == ["delete"]
    assert hot.exists() and not cold.exists()
    assert not list((tmp_path / "build" / "aa").iterdir())


def test_a_dir_whose_cargo_lock_is_taken_survives_a_real_sweep(tmp_path):
    cold, _hot = _bd(tmp_path, "aa11", 72), _bd(tmp_path, "bb22", 2)
    v = cache_tier.Verdict(str(tmp_path / "int"), False, "absent")
    with open(cold / "debug" / ".cargo-lock") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        report = cache_sweep.sweep(_roots(tmp_path, free_gib=10), v, str(tmp_path / "s.json"),
                                   NOW, True)  # fmt: skip
    assert cold.exists()
    assert str(cold) not in [d["key"] for d in report["done"]]


def test_plan_changes_nothing(tmp_path):
    cold = _bd(tmp_path, "aa11", 72)
    _bd(tmp_path, "bb22", 2)
    v = cache_tier.Verdict(str(tmp_path / "int"), False, "absent")
    report = cache_sweep.sweep(_roots(tmp_path, free_gib=10), v, str(tmp_path / "s.json"), NOW,
                               False)  # fmt: skip
    assert report["actions"] and not report["done"]
    assert cold.exists() and not (tmp_path / "s.json").exists()


def test_stamp_marks_a_use(tmp_path):
    d = _bd(tmp_path, "aa11", 72)
    before = cache_sweep.build_last_use(str(d))
    cache_sweep.stamp(str(d), NOW)
    assert cache_sweep.build_last_use(str(d)) == NOW > before + 3600


def test_only_cargo_shaped_dirs_are_objects(tmp_path):
    keep = _bd(tmp_path, "aa11", 1)
    for odd in ["aa/.evict-aa11-1", "aa/notes", "zz/aa11", "aa/AB12"]:
        (tmp_path / "build" / odd).mkdir(parents=True)
    (tmp_path / "build" / "aa" / "bb22").symlink_to(keep)
    assert cache_sweep.build_dirs(str(tmp_path / "build")) == [str(keep)]


def test_a_killed_sweeps_leftovers_are_reaped(tmp_path):
    keep = _bd(tmp_path, "aa11", 1)
    tomb = tmp_path / "build" / "aa" / ".evict-bb22-99"
    (tomb / "debug").mkdir(parents=True)
    root, _v, opener = _drive(tmp_path)
    part = root / "demoted" / "build" / "abc.part"
    (part / "debug").mkdir(parents=True)
    whole = root / "demoted" / "build" / "abd"
    whole.mkdir()
    fd = opener(_v)
    try:
        assert cache_sweep.reap_leftovers(str(tmp_path / "build"), fd) == 2
    finally:
        os.close(fd)
    assert not tomb.exists() and not part.exists() and whole.exists() and keep.exists()


def test_a_re_pointed_sccache_counts_on_the_drive(tmp_path):
    root, v, opener = _drive(tmp_path)
    (root / "sccache").mkdir()
    fd = opener(v)
    try:
        objs = cache_sweep.discover(_roots(tmp_path), str(root), fd, NOW)
    finally:
        os.close(fd)
    ext = [o for o in objs if o.key == str(root / "sccache")]  # (tmp: one device for both tiers)
    assert [(o.kind, o.tier, o.held) for o in ext] == [("sccache", "external", True)]


# GOH.CACHEEVICT2 (1): plugging or unplugging the drive re-points sccache within ONE sweep, with
# no coordinator call. A FAKE mount (lib/cache_tier.py System seams over a tmp volume) and a fake
# sccache (tests/test_sccache_repoint.py's) stand in; the live server and /Volumes are untouched.
def _fake_mount(tmp_path):
    from test_cache_tier import _sys, _volume

    mount = _volume(tmp_path, "FAKE1TB")
    present = [True]
    sysm = _sys([mount])
    sysm.mounts = lambda: [str(mount)] if present[0] else []
    return mount, present, sysm


def test_plug_and_unplug_re_point_sccache_within_one_sweep(tmp_path):
    from test_sccache_repoint import _fake

    mount, present, sysm = _fake_mount(tmp_path)
    internal = str(tmp_path / "sccache")
    (tmp_path / "fake").mkdir()
    env, state, _t = _fake(tmp_path / "fake", internal)
    env.alive = lambda: json.loads(state.read_text())["running"]
    ledger = str(tmp_path / "ledger.json")
    opener = lambda v: os.open(v.root, os.O_RDONLY | os.O_DIRECTORY)  # noqa: E731
    present[0] = False

    def one_sweep():
        verdict = cache_tier.resolve(str(tmp_path / "int"), {}, sysm)
        return cache_sweep.sweep(_roots_again(tmp_path), verdict, ledger, NOW, True, opener,
                                 sccache_env=env, sccache_internal=internal)  # fmt: skip

    (tmp_path / "cov").mkdir()
    (tmp_path / "sccache").mkdir()
    assert one_sweep()["sccache"] == "recorded"  # drive absent: the first sweep records
    present[0] = True  # plug
    assert one_sweep()["sccache"] == "repointed"
    on_drive = str(mount / "cache.noindex" / "sccache")
    assert json.loads(state.read_text())["dir"] == on_drive
    assert one_sweep()["sccache"] == "already"
    present[0] = False  # unplug
    assert one_sweep()["sccache"] == "repointed"
    assert json.loads(state.read_text())["dir"] == internal


def test_a_plan_never_re_points(tmp_path):
    from test_sccache_repoint import _fake

    _mount, _present, sysm = _fake_mount(tmp_path)
    internal = str(tmp_path / "sccache")
    (tmp_path / "fake").mkdir()
    env, state, _t = _fake(tmp_path / "fake", internal)
    env.alive = lambda: json.loads(state.read_text())["running"]
    ledger = str(tmp_path / "ledger.json")
    (tmp_path / "cov").mkdir()
    (tmp_path / "sccache").mkdir()
    ce.save(ledger, {"objs": {}, "L": 0.0, "sccache_root": internal})
    verdict = cache_tier.resolve(str(tmp_path / "int"), {}, sysm)
    opener = lambda v: os.open(v.root, os.O_RDONLY | os.O_DIRECTORY)  # noqa: E731
    r = cache_sweep.sweep(_roots_again(tmp_path), verdict, ledger, NOW, False, opener,
                          sccache_env=env, sccache_internal=internal)  # fmt: skip
    assert r["sccache"].startswith("would re-point")
    assert json.loads(state.read_text())["dir"] == internal


# GOH.CACHEEVICT2 (2): a use the evictor could not see. Cargo 1.100's build-dir layout keeps each
# unit in `<profile>/build/<pkg>/<hash>/`, so a rebuild changed none of the old probes (probed
# 2026-11-22: a touched main.rs rebuilt, build_last_use unchanged); and a wholly Fresh run writes
# nothing at all, so a goh gate stamps the build dir cargo names at its start (`stamp-for`).
def test_a_rebuild_in_the_v2_layout_is_a_use(tmp_path):
    d = _bd(tmp_path, "aa11", 72)
    unit = d / "debug" / "build" / "freshprobe" / "2ebb32fa7abedaa0" / "fingerprint"
    unit.mkdir(parents=True)
    for p in [unit.parent.parent.parent, unit.parent.parent, unit.parent, d / "debug" / "build"]:
        os.utime(p, (NOW - 72 * 3600, NOW - 72 * 3600))
    os.utime(d / "debug", (NOW - 72 * 3600, NOW - 72 * 3600))
    os.utime(unit, (NOW, NOW))
    assert cache_sweep.build_last_use(str(d)) == NOW


def test_stamp_for_marks_the_build_dir_cargo_names(tmp_path):
    d = _bd(tmp_path, "aa11", 72)
    cache_sweep.stamp(str(d), 1000.0)  # an old use
    assert cache_sweep.stamp_for(str(tmp_path), build_dir=lambda _c: str(d)) == str(d)
    assert (d / cache_sweep.STAMP).stat().st_mtime > 1000.0


def test_stamp_for_never_makes_a_build_dir(tmp_path):
    gone = tmp_path / "build" / "cc" / "cc33"
    assert cache_sweep.stamp_for(str(tmp_path), build_dir=lambda _c: str(gone)) is None
    assert cache_sweep.stamp_for(str(tmp_path), build_dir=lambda _c: None) is None
    assert not gone.exists()


def test_the_rust_gate_stamps_before_its_checks():
    rust = (REPO_ROOT / "gates" / "rust_gate.sh").read_text()
    assert rust.index('cache_sweep.py" stamp-for') < rust.index("rust_crate_checks() {")
