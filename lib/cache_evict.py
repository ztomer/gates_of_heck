#!/usr/bin/env python3
"""ONE value-aware evictor over the build caches (GOH.CACHEEVICT phase 2).

Three policies fought over one disk: the scripts reclaimer's BUILD_ROOT_MAX_GIB ceiling over
~/.cargo/build, goh's llvm-cov byte budget (gates/_cov_cache.py) and sccache's own LRU cap. Each
evicted what another had just rebuilt (68 GiB of build dirs all rewritten within 5 h, 2026-11-22).
This module is the one ranking and the one budget; lib/cache_sweep.py finds the objects and acts.

Ranking: GreedyDual-Size-Frequency (Cao & Irani, USITS 1997; Cherkasova, HPL-98-69, 1998):

    H(o) = l(o) + F(o) x C(o) / S(o)

* S: bytes on disk. C: seconds to get the object back: a rebuild (BUILD_COST_S, COV_COST_S) when
  it would be deleted, the promote copy (bytes / PROMOTE_BPS) when it would be demoted to the
  removable tier (lib/cache_move.py), so a cheap demotion goes before a costly deletion.
* F: uses, decayed with a HALF_LIFE_S half-life. A use is read from the object's own last-use stamp
  (`.goh-used`, a cargo lock, the dir mtime) at every sweep: recency of USE, never of write.
* l(o): the inflation clock L as it stood at the object's last use. L rises to the H of each object
  evicted, so an object nobody uses falls behind the clock and goes, with no TTL.

Never evicted: an object a live build holds (`held`: a cargo `.cargo-build-lock` or `.cargo-lock`
flock taken, a live pid in an llvm-cov `.goh-running` marker, or a use within GRACE_S), and the
sccache directory while its server lives (its bytes count in the budget; its own LRU, capped by
`sccache_cap`, is the backstop).

Budget, internal disk: B = min(0.5 x (held + free), held + free - floor - RESERVE_GIB), the floor
GOH_LANE_MIN_FREE_GIB (lib/capacity.py, the lanes' one floor). With the removable tier present the
internal tier keeps only its HOT_GIB hot set and overflow DEMOTES; the large tier holds EXT_GIB
(owner 2026-11-22: 250 GiB to start) under its own free floor, and its overflow is deleted.

The ledger (uses, last use, l per object, and L) is JSON keyed by the object's path, a stable
identity, at GOH_CACHE_EVICT_STATE (default ~/.local/state/goh/cache-evict.json).

The ONE value-aware evictor's ranking and budget (GOH.CACHEEVICT phase 2), replacing three fighting
policies (the scripts build-root ceiling, the llvm-cov byte budget, sccache's own LRU): GDSF `H = l
+ F x C / S` -- S bytes, C the seconds to get the object back (a rebuild, or the promote copy when
it would be demoted), F uses decayed with a 24 h half-life and read from last-use stamps (recency
of use, never of write), l the clock L at the last use, L raised to each evicted H. Never an object
a live build holds (`held`: a cargo `.cargo-lock` flock taken, a live llvm-cov `.goh-running` pid,
a use within 10 min) nor the sccache dir (counted, its server owns it). Internal budget `min(0.5 x
(held+free), held+free - GOH_LANE_MIN_FREE_GIB - 10 GiB)`, held to `HOT_GIB` (60) with the large
tier present, whose overflow demotes; the large tier's `EXT_GIB` (250) under its own floor, whose
overflow deletes. Ledger at GOH_CACHE_EVICT_STATE, its objects `evict_objs` rows, keyed by path.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cache_rows  # lib/, beside this file
import lock_order

GIB = 1 << 30
HALF_LIFE_S = 86400.0
GRACE_S = 600.0
HOT_GIB = 60.0  # rules.cache_policy.removable_tier.budget: internal hot tier 40-60 GiB
EXT_GIB = 250.0  # the same ruling: the large tier starts at 250 GiB
RESERVE_GIB = 10.0  # one build in flight on top of the floor (gates/_cov_cache.py RESERVE_GIB)
BUILD_COST_S = 370.0  # a cold cargo build dir: 300-440 s measured 2026-11-22
COV_COST_S = 600.0  # an instrumented build: RUSTC_WRAPPER="" (gates/_coverage_rust.sh), no sccache
PROMOTE_BPS = 4.3 * GIB / 12.4  # a 4.3 GiB build dir came back in 12.4 s at load ~40
SCCACHE_GIB = 40  # sccache's share: its own LRU is the backstop at this cap (lib/sccache_cap.py
# writes it; GOH.SCCACHECAP's 48 h measurement decides whether 40 stays)
MOVABLE = frozenset({"build", "llvm-cov"})
TRIVIAL = 1 << 20  # under this, a demotion would copy nothing worth keeping: delete instead
COV_MARKER = ".goh-running"


@dataclass
class Obj:
    key: str  # its path: the stable identity
    kind: str  # build | llvm-cov | sccache
    tier: str  # internal | external
    bytes: int
    last_use: float
    held: bool = False
    uses: float = 1.0
    l: float = 0.0  # noqa: E741 -- the clock at the last use, as the papers write it

    @property
    def rebuild_s(self) -> float:
        return COV_COST_S if self.kind == "llvm-cov" else BUILD_COST_S


def decayed(uses: float, since: float) -> float:
    return uses * 0.5 ** (max(0.0, since) / HALF_LIFE_S)


def priority(o: Obj, now: float, cost_s: float | None = None) -> float:
    """H = l + F x C / S, F the use count decayed to `now`; C the rebuild unless given."""
    cost = o.rebuild_s if cost_s is None else cost_s
    return o.l + decayed(o.uses, now - o.last_use) * cost / max(o.bytes, 1)


def demote_cost(o: Obj) -> float:
    return o.bytes / PROMOTE_BPS


def budget(held: int, free: int, floor_gib: float, reserve_gib: float = RESERVE_GIB) -> int:
    total = held + free
    return max(0, min(total // 2, total - int((floor_gib + reserve_gib) * GIB)))


def ext_budget(held: int, free: int, floor: int, cap_gib: float = EXT_GIB) -> int:
    return max(0, min(int(cap_gib * GIB), held + free - floor))


@dataclass
class Action:
    verb: str  # demote | delete
    obj: Obj
    h: float


def plan(objs: list[Obj], internal: int, external: int | None, clock: float,
         now: float) -> tuple[list[Action], float]:  # fmt: skip
    """The actions that bring each tier under its budget, lowest H first, and the new clock.
    Internal overflow demotes what can move; the large tier then sheds its lowest H, the objects
    just demoted included (each of those becomes a plain delete, never copied out to be dropped).
    `external` None: no large tier, the internal tier evicts by deletion alone."""
    acts: list[Action] = []
    held_in = sum(o.bytes for o in objs if o.tier == "internal")
    held_ex = sum(o.bytes for o in objs if o.tier == "external")
    ext_objs = [o for o in objs if o.tier == "external"]
    planned: dict[int, Obj] = {}  # id(a planned copy) -> the internal object it would copy

    def cost(o: Obj) -> float:
        can = external is not None and o.kind in MOVABLE and TRIVIAL <= o.bytes <= external
        return demote_cost(o) if can else o.rebuild_s

    cands = [o for o in objs if o.tier == "internal" and not o.held and o.kind != "sccache"]
    for o in sorted(cands, key=lambda c: priority(c, now, cost(c))):
        if held_in <= internal:
            break
        demote = external is not None and o.kind in MOVABLE and TRIVIAL <= o.bytes <= external
        h = priority(o, now, demote_cost(o) if demote else None)
        acts.append(Action("demote" if demote else "delete", o, h))
        clock = max(clock, h)
        held_in -= o.bytes
        if demote:
            for old in [e for e in ext_objs if e.key == o.key and id(e) not in planned]:
                ext_objs.remove(old)  # the copy overwrites an older one of the same dir
                held_ex -= old.bytes
            held_ex += o.bytes
            copy = Obj(o.key, o.kind, "external", o.bytes, o.last_use, False, o.uses, o.l)
            ext_objs.append(copy)
            planned[id(copy)] = o
    if external is not None and held_ex > external:
        movable = [o for o in ext_objs if not o.held and o.kind != "sccache"]
        for o in sorted(movable, key=lambda c: priority(c, now)):
            if held_ex <= external:
                break
            h = priority(o, now)
            victim = planned.get(id(o), o)
            if victim is not o:  # no room after all: delete it where it is, never copy it out
                acts = [a for a in acts if a.obj is not victim]
            acts.append(Action("delete", victim, h))
            clock = max(clock, h)
            held_ex -= o.bytes
    return acts, clock


# -- the ledger: uses and clocks by stable identity --------------------------------------------


def state_path() -> str:
    """The ledger's name; its record is goh.db's `kv` row beside it (read_ledger, GOH.STATEDBS3)."""
    import small_stores  # lib/: the database beside the ledger's name
    import state_paths

    return os.environ.get("GOH_CACHE_EVICT_STATE") or os.path.join(
        state_paths.state_root(), small_stores.KV_FILES["cache-evict"])  # fmt: skip


def read_ledger(path: str) -> dict:
    """The ledger PATH names: goh.db's row beside it (the file before its import), as `load`."""
    import small_stores  # lib/: goh.db's kv (GOH.STATEDBS3); a worker's own ledger is a file

    data = small_stores.get(path)
    if isinstance(data, dict) and isinstance(data.get("objs"), dict):
        return {**data, "objs": cache_rows.newer(cache_rows.objs(path), data["objs"])}
    return {"L": 0.0, "objs": {}}


def write_ledger(path: str, data: dict) -> None:
    """The scalars to kv, the objects to evict_objs (GOH.KVBLOBS); all to kv when the cache takes
    none. `objs` stays, empty: an older copy reads no ledger without it."""
    import small_stores

    kept = cache_rows.put_objs(path, data["objs"])
    small_stores.put(path, {**data, "objs": {}} if kept else data)


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and isinstance(data.get("objs"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"L": 0.0, "objs": {}}


def save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)


def observe(data: dict, objs: list[Obj]) -> None:
    """Fold each object's last-use stamp into the ledger: a stamp newer than the ledger's is one use
    (the count decayed to then, plus one) and resets l to the clock. Objects gone are dropped;
    an object first seen starts at one use and the current clock."""
    clock = float(data.get("L", 0.0))
    seen = {}
    for o in objs:
        rec = data["objs"].get(o.key)
        if rec is None:
            rec = {"uses": 1.0, "at": o.last_use, "l": clock}
        elif o.last_use > rec["at"] + 1:
            rec = {"uses": decayed(rec["uses"], o.last_use - rec["at"]) + 1.0, "at": o.last_use,
                   "l": clock}  # fmt: skip
        o.uses, o.l = rec["uses"], rec["l"]
        o.last_use = max(o.last_use, rec["at"])
        seen[o.key] = rec
    data["objs"] = seen


# -- what a live build holds -------------------------------------------------------------------


def _flock_taken(path: str) -> bool:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        lock_order.flock(fd, "cargo_build", wait=False)
    except BlockingIOError:
        return True
    except OSError:
        return False
    finally:
        os.close(fd)  # closing drops a lock we took
    return False


# The lock a running cargo holds in a dir: `.cargo-build-lock` in a separate build-dir (cargo
# 1.100, probed 2026-11-22), `.cargo-lock` in a target dir. Only the first was missed, and the
# sweep demoted a release's build dir mid-build (GOH.BUILDDIRVANISH).
CARGO_LOCK_NAMES = (".cargo-build-lock", ".cargo-lock")


@contextlib.contextmanager
def locks_held(path: str):
    """Every cargo lock under `path`, taken without waiting; BlockingIOError if one is held. Every
    goh removal of a build dir renames it away inside this (lib/cache_sweep.py, build_dir_reap)."""
    fds = []
    try:
        for lock in cargo_locks(path):
            fd = os.open(lock, os.O_RDONLY)
            fds.append(fd)
            lock_order.flock(fd, "cargo_build", wait=False)
        yield
    finally:
        for fd in fds:
            os.close(fd)


def cargo_locks(path: str) -> list[str]:
    """Every CARGO_LOCK_NAMES file at `<dir>/<profile>/` and `<dir>/<target>/<profile>/`."""
    base = glob.escape(path)
    return [p for name in CARGO_LOCK_NAMES for depth in (("*",), ("*", "*"))
            for p in glob.glob(os.path.join(base, *depth, name))]  # fmt: skip


def _cov_running(path: str) -> bool:
    import sys

    gates = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gates")
    if gates not in sys.path:
        sys.path.insert(0, gates)
    import _head_cache

    return _head_cache.running(path, COV_MARKER)


def held(path: str, last_use: float, now: float, running=_cov_running) -> bool:
    """A live build holds `path`: a cargo lock is taken, a marker names a live pid, or it was
    used within GRACE_S (the gap before either lands)."""
    if now - last_use < GRACE_S:
        return True
    if any(_flock_taken(lock) for lock in cargo_locks(path)):
        return True
    with contextlib.suppress(OSError):
        if running(path):
            return True
    return False


def now() -> float:
    return time.time()
