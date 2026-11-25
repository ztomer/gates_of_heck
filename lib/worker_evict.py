#!/usr/bin/env python3
"""The remote test worker's evictor (GOH.REMOTEEVICT): goh's one ranking (lib/cache_evict.py,
GOH.CACHEEVICT) over the worker's own caches, under the worker's own rules.

`goh remote-shard` runs it inside the worker container at the end of every remote run. The worker
dir (GOH_REMOTE_DIR, /work in the container) sits on the host's transcode-scratch disk, which the
media server's transcodes share (owner 2026-11-22), so its budget is

    B = min(ceiling, held + free - floor)        ceiling 80 GB, floor 100 GB free

and the objects over B go coldest first, by the same GreedyDual-Size-Frequency H the Mac's sweep
uses. The probe refuses a run over the ceiling or under the floor (tools/remote_shard.py); this is
what brings the dir back under both without a manual step.

Objects, each keyed by its path (a stable identity) in a ledger beside them (cache/evict.json):
* a lane slot's cargo target, `tree/target` and `trees/<slot>/target` (kind build);
* the cargo home `cache/home/.cargo` (the registry; its fetch stamps go with it, so the next run
  fetches again) (kind build);
* goh's llvm-cov entries `cache/home/.cache/goh/llvm-cov/<key>` (kind llvm-cov);
* sccache's dir `cache/home/.cache/sccache`, counted and never swept: its own LRU, capped by the
  image's SCCACHE_CACHE_SIZE, is the backstop, as on the Mac.
What the run just used is stamped (`.goh-used`) and held for cache_evict.GRACE_S, so a sweep never
takes the slot that just ran; a cargo lock held by another slot's build holds that slot.

usage: worker_evict.py [plan|run] --root DIR [--ceiling-gib N] [--floor-gib N] [--json]

`lib/cache_evict.py`'s ranking over the worker's caches (each lane slot's cargo target, the cargo
home, llvm-cov entries; sccache counted, never swept) under its own budget `min(80 GB ceiling, held
+ free - 100 GB floor)` on the scratch disk the host's transcodes share. `goh remote-shard` runs it
in the container after every run, what the run used stamped first.

`lib/worker_share.py`: `worker_share.py`, `worker_share.sh`: THE WORKER SHARE (GOH.POOLSHARE): how
WIDE an admitted gate runs, where admission says how many -- cores / the gates admitted now (either
lane, itself included), exported at admission as `GOH_GATE_WORKERS` and the variables runners read
(xdist, cargo, libtest, nextest; never one that widens a serial runner), split among
`local_ci.sh`'s concurrent steps, counted afresh by a run under no slot (`tools/pytest.sh` as a
land's pre-step) and by a layer under a consumer gate.sh (`goh_workers_ensure`); `goh_workers` is
the flag for swift, xcodebuild and make.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: run as is
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import glob
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cache_evict as ce  # noqa: E402
import cache_sweep as cs  # noqa: E402

CEILING_GIB = 80.0
FLOOR_GIB = 100.0


def budget(held: int, free: int, ceiling_gib: float, floor_gib: float) -> int:
    """The worker's bytes: under its ceiling, and never past the disk's free-space floor."""
    return max(0, min(int(ceiling_gib * ce.GIB), held + free - int(floor_gib * ce.GIB)))


def paths(root: str) -> dict[str, list[str]]:
    """The worker's cache objects under `root`, by kind."""
    home = os.path.join(root, "cache", "home")
    targets = [os.path.join(root, "tree", "target")]
    targets += sorted(glob.glob(os.path.join(glob.escape(root), "trees", "*", "target")))
    builds = [p for p in [*targets, os.path.join(home, ".cargo")] if os.path.isdir(p)]
    sccache = os.path.join(home, ".cache", "sccache")
    return {
        "build": builds,
        "llvm-cov": cs.cov_entries(os.path.join(home, ".cache", "goh", "llvm-cov")),
        "sccache": [sccache] if os.path.isdir(sccache) else [],
    }


def discover(root: str, now: float, size=cs._head_cache.tree_bytes) -> list[ce.Obj]:
    roots = cs.Roots(root, root, root, root, size=size)
    found = paths(root)
    objs = [cs._obj(p, kind, "internal", p, roots, now) for kind in ("build", "llvm-cov")
            for p in found[kind]]  # fmt: skip
    objs += [ce.Obj(p, "sccache", "internal", size(p), now, True) for p in found["sccache"]]
    return objs


def reap(root: str) -> int:
    """A killed sweep's `.evict-*` tombstones beside the objects: only a sweep makes them."""
    heads = {os.path.dirname(p) for kind in paths(root).values() for p in kind}
    heads |= {os.path.join(root, "tree"), os.path.join(root, "cache", "home")}
    tombs = [t for h in heads for t in glob.glob(os.path.join(glob.escape(h), ".evict-*"))]
    for tomb in tombs:
        shutil.rmtree(tomb, ignore_errors=True)
    return len(tombs)


def sweep(root: str, now: float, execute: bool, ceiling_gib: float = CEILING_GIB,
          floor_gib: float = FLOOR_GIB, size=cs._head_cache.tree_bytes,
          free=lambda p: shutil.disk_usage(p).free) -> dict:  # fmt: skip
    state = os.path.join(root, "cache", "evict.json")
    if execute:
        reap(root)
    data = ce.load(state)
    objs = discover(root, now, size)
    ce.observe(data, objs)
    held = sum(o.bytes for o in objs)
    free_b = free(root)
    b = budget(held, free_b, ceiling_gib, floor_gib)
    acts, clock = ce.plan(objs, b, None, float(data.get("L", 0.0)), now)
    done = cs.act(acts, None) if execute else []
    if execute:
        data["L"] = clock
        ce.save(state, data)
    return {"held": held, "free": free_b, "budget": b,
            "actions": [{"verb": a.verb, "key": a.obj.key, "bytes": a.obj.bytes} for a in acts],
            "done": [{"key": k, "ok": ok} for _, k, ok in done]}  # fmt: skip


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="worker_evict.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("verb", nargs="?", choices=("plan", "run"), default="plan")
    ap.add_argument("--root", required=True)
    ap.add_argument("--ceiling-gib", type=float, default=CEILING_GIB)
    ap.add_argument("--floor-gib", type=float, default=FLOOR_GIB)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = sweep(a.root, ce.now(), a.verb == "run", a.ceiling_gib, a.floor_gib)
    if a.json:
        print(json.dumps(r, indent=1))
        return 0
    gone = sum(x["bytes"] for x in r["actions"])
    print(f"worker cache: {cs._gib(r['held'])} held, {cs._gib(r['free'])} free, budget "
          f"{cs._gib(r['budget'])}; {len(r['actions'])} to evict ({cs._gib(gone)})")  # fmt: skip
    for x, d in zip(r["actions"], r["done"] or [None] * len(r["actions"]), strict=True):
        state = "" if d is None else (" done" if d["ok"] else " skipped (held or gone)")
        print(f"  {cs._gib(x['bytes']):>10}  {x['key']}{state}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
