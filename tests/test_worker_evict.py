"""The remote worker's evictor (GOH.REMOTEEVICT): an 80 GB ceiling and a 100 GB free-space floor on
the scratch disk the host's transcodes share, enforced coldest first at the end of each run."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import cache_evict as ce
import worker_evict as we

GIB = ce.GIB
NOW = 2_000_000_000.0


def _obj(root: Path, rel: str, age_h: float) -> Path:
    """A cache object `rel` under the worker dir, last used `age_h` hours before NOW."""
    p = root / rel
    (p / "debug" / "deps").mkdir(parents=True)
    t = NOW - age_h * 3600
    for d in (p / ".goh-used", p / "debug" / "deps", p / "debug", p):
        if d.name == ".goh-used":
            d.write_text("")
        os.utime(d, (t, t))
    return p


def _fixture(root: Path) -> dict[str, Path]:
    return {
        "hot": _obj(root, "trees/2/target", 0.05),  # the run that just ended: held (grace)
        "warm": _obj(root, "trees/1/target", 2),
        "cold": _obj(root, "trees/0/target", 48),
        "colder": _obj(root, "tree/target", 200),
    }


def _size(p: str) -> int:
    return 10 * GIB  # each object 10 GiB, whatever is on disk


def test_the_budget_is_the_ceiling_or_the_floor_whichever_bites():
    assert we.budget(50 * GIB, 500 * GIB, 80, 100) == 80 * GIB  # the ceiling
    assert we.budget(50 * GIB, 120 * GIB, 80, 100) == 70 * GIB  # 100 GB must stay free
    assert we.budget(50 * GIB, 20 * GIB, 80, 100) == 0  # the floor already crossed


def test_a_crossed_floor_is_evicted_coldest_first_down_to_the_floor(tmp_path: Path):
    objs = _fixture(tmp_path)
    # 40 GiB held, 85 GiB free: 15 GiB under the 100 GB floor, so two 10 GiB objects must go.
    r = we.sweep(str(tmp_path), NOW, True, 80, 100, size=_size, free=lambda p: 85 * GIB)
    assert r["budget"] == 25 * GIB
    assert [a["key"] for a in r["actions"]] == [str(objs["colder"]), str(objs["cold"])]
    assert all(d["ok"] for d in r["done"])
    assert not objs["colder"].exists() and not objs["cold"].exists()
    assert objs["warm"].exists() and objs["hot"].exists()
    assert (tmp_path / "cache" / "evict.json").exists()  # the ledger, beside the caches


def test_what_the_run_just_used_is_never_taken_even_over_budget(tmp_path: Path):
    objs = _fixture(tmp_path)
    r = we.sweep(str(tmp_path), NOW, True, 80, 100, size=_size, free=lambda p: 0)
    assert str(objs["hot"]) not in [a["key"] for a in r["actions"]]
    assert objs["hot"].exists()


def test_under_both_rules_nothing_moves_and_plan_never_acts(tmp_path: Path):
    objs = _fixture(tmp_path)
    r = we.sweep(str(tmp_path), NOW, True, 80, 100, size=_size, free=lambda p: 500 * GIB)
    assert r["actions"] == []
    r = we.sweep(str(tmp_path), NOW, False, 80, 100, size=_size, free=lambda p: 0)
    assert r["actions"] and r["done"] == []
    assert all(p.exists() for p in objs.values())


def test_sccache_is_counted_and_never_swept(tmp_path: Path):
    _fixture(tmp_path)
    sc = tmp_path / "cache" / "home" / ".cache" / "sccache"
    sc.mkdir(parents=True)
    r = we.sweep(str(tmp_path), NOW, True, 80, 100, size=_size, free=lambda p: 0)
    assert r["held"] == 50 * GIB
    assert str(sc) not in [a["key"] for a in r["actions"]] and sc.exists()
