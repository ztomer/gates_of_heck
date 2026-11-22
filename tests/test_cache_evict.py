"""ONE value-aware evictor: GDSF over build dirs, llvm-cov entries and sccache (GOH.CACHEEVICT).

The design's red-first cases (design.md section 6): a recently used dir survives a stale one of the
same size; a held dir never goes; the budget shrinks with free space; the clock lets an old, once
popular object go; a cheap demotion goes before a costly deletion; the large tier sheds its own
overflow. All on fixture objects and tmp dirs; nothing touches ~/.cargo/build or sccache.
"""

from __future__ import annotations

import fcntl
import os
import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import cache_evict as ce

GIB = 1 << 30
NOW = 1_800_000_000.0
DAY = 86400.0


def _o(key, gib=10, age_h=1.0, kind="build", tier="internal", held=False, uses=1.0, l=0.0):  # noqa: E741
    return ce.Obj(key, kind, tier, int(gib * GIB), NOW - age_h * 3600, held, uses, l)


def _verbs(acts):
    return [(a.verb, a.obj.key) for a in acts]


def test_recent_use_survives_a_stale_dir_of_the_same_size():
    objs = [_o("fresh", age_h=1), _o("stale", age_h=72)]
    acts, _ = ce.plan(objs, 10 * GIB, None, 0.0, NOW)
    assert _verbs(acts) == [("delete", "stale")]


def test_a_held_dir_is_never_evicted_even_at_the_lowest_priority():
    objs = [_o("held", age_h=500, held=True), _o("hot", age_h=1, uses=50)]
    acts, _ = ce.plan(objs, 5 * GIB, None, 0.0, NOW)
    assert _verbs(acts) == [("delete", "hot")]


def test_sccache_bytes_count_but_its_dir_is_never_swept():
    objs = [_o("sccache", gib=20, age_h=999, kind="sccache"), _o("b", gib=10, age_h=1)]
    acts, _ = ce.plan(objs, 25 * GIB, None, 0.0, NOW)
    assert _verbs(acts) == [("delete", "b")]


def test_the_budget_shrinks_as_free_space_falls():
    held = 100 * GIB
    roomy = ce.budget(held, 300 * GIB, floor_gib=30)
    falling = ce.budget(held, 30 * GIB, floor_gib=30)
    tight = ce.budget(50 * GIB, 20 * GIB, floor_gib=30)
    assert roomy == 200 * GIB  # half of held + free
    assert falling == 65 * GIB < roomy
    assert tight == 30 * GIB  # held + free - floor - reserve
    assert ce.budget(held, 0, floor_gib=200) == 0


def test_the_clock_lets_an_old_popular_object_go():
    """Once popular (high F) but unused for weeks: F decays, and the clock raised by each eviction
    passes its l, so it goes before a fresh object first used at the higher clock."""
    old = _o("old", age_h=24 * 21, uses=40, l=0.0)
    acts, clock = ce.plan([old, _o("x", age_h=1)], 10 * GIB, None, 0.0, NOW)
    assert clock > 0
    fresh = _o("fresh", age_h=1, uses=1, l=clock)
    acts, _ = ce.plan([old, fresh], 10 * GIB, None, clock, NOW)
    assert _verbs(acts) == [("delete", "old")]


def test_frequency_outranks_a_single_recent_use_of_equal_age():
    objs = [_o("once", age_h=2, uses=1), _o("daily", age_h=2, uses=6)]
    acts, _ = ce.plan(objs, 10 * GIB, None, 0.0, NOW)
    assert _verbs(acts) == [("delete", "once")]


def test_overflow_demotes_when_the_large_tier_has_room():
    objs = [_o("cold", age_h=48), _o("warm", age_h=1), _o("cov", age_h=50, kind="llvm-cov")]
    acts, _ = ce.plan(objs, 10 * GIB, 250 * GIB, 0.0, NOW)
    assert sorted(_verbs(acts)) == [("demote", "cold"), ("demote", "cov")]


def test_the_large_tier_sheds_its_lowest_priority_by_deletion():
    objs = [_o("in", age_h=40), _o("keep", age_h=1), _o("ext-old", age_h=400, tier="external"),
            _o("ext-new", age_h=2, tier="external")]  # fmt: skip
    acts, _ = ce.plan(objs, 10 * GIB, 20 * GIB, 0.0, NOW)
    assert ("demote", "in") in _verbs(acts) and ("delete", "ext-old") in _verbs(acts)
    assert ("delete", "ext-new") not in _verbs(acts)


def test_a_demotion_with_no_room_is_a_plain_delete_never_a_copy():
    objs = [_o("in", age_h=900), _o("keep", age_h=1), _o("ext", gib=10, age_h=1, uses=9,
                                                         tier="external")]  # fmt: skip
    acts, _ = ce.plan(objs, 10 * GIB, 10 * GIB, 0.0, NOW)
    assert _verbs(acts) == [("delete", "in")] and acts[0].obj.tier == "internal"


def test_the_ledger_counts_uses_from_stamps_and_drops_the_gone(tmp_path):
    state = tmp_path / "s.json"
    data = ce.load(str(state))
    a, b = _o("a", age_h=10), _o("b", age_h=10)
    ce.observe(data, [a, b])
    data["L"] = 7.0
    ce.save(str(state), data)
    data = ce.load(str(state))
    a2 = _o("a", age_h=0.5)  # used again since: one more use, l reset to the clock
    ce.observe(data, [a2])
    assert set(data["objs"]) == {"a"}
    assert 1.5 < a2.uses < 2.0 and a2.l == 7.0
    a3 = _o("a", age_h=0.5)  # no new use: the count stays
    ce.observe(data, [a3])
    assert a3.uses == a2.uses


def test_held_reads_a_taken_cargo_lock_and_recent_use(tmp_path):
    d = tmp_path / "bd"
    (d / "debug").mkdir(parents=True)
    lock = d / "debug" / ".cargo-lock"
    lock.write_text("")
    never = lambda p: False  # noqa: E731
    assert not ce.held(str(d), NOW - DAY, NOW, running=never)
    assert ce.held(str(d), NOW - 60, NOW, running=never)
    with open(lock) as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        assert ce.held(str(d), NOW - DAY, NOW, running=never)
    assert ce.held(str(d), NOW - DAY, NOW, running=lambda p: True)
    nested = tmp_path / "bd2" / "aarch64-apple-darwin" / "release"
    nested.mkdir(parents=True)
    (nested / ".cargo-lock").write_text("")
    with open(nested / ".cargo-lock") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        assert ce.held(str(tmp_path / "bd2"), NOW - DAY, NOW, running=never)
    assert os.path.exists(lock)


def test_ext_budget_keeps_the_large_tier_free_floor():
    assert ce.ext_budget(0, 951 * GIB, 50 * GIB) == 250 * GIB
    assert ce.ext_budget(100 * GIB, 60 * GIB, 50 * GIB) == 110 * GIB


def test_a_near_empty_dir_is_never_copied_out():
    """A demotion's cost is its copy, so a near-empty dir would rank first and be copied for
    nothing; under TRIVIAL it ranks by its rebuild instead, and a tiny dir is cheap to keep."""
    objs = [_o("empty", gib=0.0001, age_h=90), _o("big", age_h=1), _o("cold", age_h=80)]
    acts, _ = ce.plan(objs, 11 * GIB, 250 * GIB, 0.0, NOW)
    assert _verbs(acts) == [("demote", "cold")]
