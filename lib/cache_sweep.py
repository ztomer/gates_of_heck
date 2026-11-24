#!/usr/bin/env python3
"""The evictor's sweep: find every cache object, rank them ONCE, act (GOH.CACHEEVICT phase 2).

Objects (lib/cache_evict.py ranks them):
* cargo build dirs `<CARGO_HOME>/build/<h>/<h>` (~/.cargo/config.toml's build-dir template);
* goh's llvm-cov entries `~/.cache/goh/llvm-cov/<key>` (gates/_cov_cache.py);
* the sccache directory, counted and never swept: its server owns its files;
* the demoted copies on the removable tier (lib/cache_tier.py, lib/cache_move.py), keyed by the
  internal path they came from, so one object keeps one ledger record across tiers.

A build dir's last use is the newest of its `.goh-used` stamp, its cargo locks, its profile and
deps dirs and (cargo 1.100's layout) each unit's fingerprint dir; a build cargo finds wholly Fresh
writes none of them, so goh's rust gate stamps the dir at its start (lib/cache_move.py
`stamp-for`). Named deviation: a Fresh build outside goh is not seen as a use until something
stamps it.

Each run also lets sccache follow the drive (lib/sccache_repoint.py `follow`): a drive that came
or went since the last sweep re-points the server within this sweep (GOH.CACHEEVICT2).

Acting: a delete renames the object away first (under the cargo locks it holds, or the llvm-cov
marker lock via gates/_head_cache.py's `_evict`), so no build starts in a directory being removed;
a demotion copies while holding the cargo locks, so a cargo that starts meanwhile waits for it.
One sweep at a time (a lock beside the ledger); a second exits 0 at once.

usage: cache_sweep.py [plan|run] [--json]     plan (default) prints, run acts
       cache_sweep.py stamp PATH              mark PATH used now
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

import contextlib
import glob
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(HERE, "..", "gates"))
import _head_cache  # gates/  # noqa: E402
import cache_evict as ce  # noqa: E402
import cache_move  # noqa: E402
import cache_tier  # noqa: E402
import lock_order  # noqa: E402
import sccache_repoint  # noqa: E402

STAMP = cache_move.STAMP
_HEX = re.compile(r"^[0-9a-f]+$")


@dataclass
class Roots:
    build: str
    cov: str
    sccache: str
    internal: str  # the volume the hot tier lives on (free space is read there)
    floor_gib: float = 30.0
    size: object = field(default=_head_cache.tree_bytes)
    free: object = field(default=lambda p: shutil.disk_usage(p).free)


def default_roots() -> Roots:
    sys.path.insert(0, HERE)
    import capacity

    home = os.path.expanduser("~")
    cargo = os.environ.get("CARGO_HOME") or os.path.join(home, ".cargo")
    sccache = os.environ.get("SCCACHE_DIR") or sccache_repoint.internal_dir()
    return Roots(os.path.join(cargo, "build"), os.path.join(home, ".cache", "goh", "llvm-cov"),
                 sccache, home, capacity.settings()["GOH_LANE_MIN_FREE_GIB"])  # fmt: skip


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def build_last_use(path: str) -> float:
    probes = [os.path.join(path, STAMP), path, *ce.cargo_locks(path)]
    probes += glob.glob(os.path.join(glob.escape(path), "*", "deps"))
    # cargo 1.100's layout: one dir per unit, `<profile>/build/<pkg>/<hash>/`; a rebuild rewrites
    # that unit's fingerprint dir and none of the probes above (GOH.CACHEEVICT2, probed 2026-11-22)
    probes += glob.glob(os.path.join(glob.escape(path), "*", "build", "*", "*", "fingerprint"))
    probes += [p for p in glob.glob(os.path.join(glob.escape(path), "*")) if os.path.isdir(p)]
    return max(_mtime(p) for p in probes)


def build_dirs(root: str) -> list[str]:
    """Only cargo's own shape, `<hex>/<hex>` ({workspace-path-hash}): anything else under the
    build root is not the evictor's to judge (a tombstone, a hand-made dir)."""
    found = glob.glob(os.path.join(glob.escape(root), "*", "*"))
    return sorted(p for p in found if os.path.isdir(p) and not os.path.islink(p)
                  and _HEX.match(os.path.basename(p))
                  and _HEX.match(os.path.basename(os.path.dirname(p))))  # fmt: skip


def cov_entries(root: str) -> list[str]:
    try:
        found = os.scandir(root)
        return sorted(e.path for e in found if e.is_dir(follow_symlinks=False)
                      and not e.name.startswith("."))  # fmt: skip
    except OSError:
        return []


def _obj(key: str, kind: str, tier: str, path: str, roots: Roots, now: float) -> ce.Obj:
    used = build_last_use(path) if kind == "build" else _head_cache.last_use(path)
    return ce.Obj(key, kind, tier, roots.size(path), used, ce.held(path, used, now))


def demoted_objs(root: str, root_fd: int | None, roots: Roots, now: float) -> list[ce.Obj]:
    """The large tier's copies, each keyed by its `.goh-origin` (the internal path). Read by path
    (reads only), each directory checked to sit on the device the verified fd names."""
    out = []
    if root_fd is None:
        return out
    dev = os.fstat(root_fd).st_dev
    for kind in sorted(ce.MOVABLE):
        pattern = os.path.join(glob.escape(root), cache_move.DEMOTED, kind, "*")
        for path in sorted(glob.glob(pattern)):
            if path.endswith(".part") or _dev(path) != dev:
                continue
            try:
                with open(os.path.join(path, cache_move.ORIGIN), encoding="utf-8") as handle:
                    key = handle.read().strip()
            except OSError:
                continue
            o = _obj(key, kind, "external", path, roots, now)
            o.held = False  # nothing builds on the large tier
            out.append(o)
    return out


def _dev(path: str) -> int:
    try:
        return os.stat(path).st_dev
    except OSError:
        return -1


def discover(roots: Roots, root: str, root_fd: int | None, now: float) -> list[ce.Obj]:
    objs = [_obj(p, "build", "internal", p, roots, now) for p in build_dirs(roots.build)]
    objs += [_obj(p, "llvm-cov", "internal", p, roots, now) for p in cov_entries(roots.cov)]
    if os.path.isdir(roots.sccache):
        on_ext = root_fd is not None and os.stat(roots.sccache).st_dev == os.fstat(root_fd).st_dev
        objs.append(ce.Obj(roots.sccache, "sccache", "external" if on_ext else "internal",
                           roots.size(roots.sccache), now, True))  # fmt: skip
    ext_sccache = os.path.join(root, sccache_repoint.SUBDIR) if root_fd is not None else ""
    if (
        ext_sccache
        and ext_sccache != roots.sccache
        and _dev(ext_sccache) == os.fstat(root_fd).st_dev
    ):
        # the server re-pointed to the drive (lib/sccache_repoint.py): its bytes count there
        objs.append(ce.Obj(ext_sccache, "sccache", "external", roots.size(ext_sccache), now, True))
    return objs + demoted_objs(root, root_fd, roots, now)


_locks_held = ce.locks_held  # the one taker, shared with lib/build_dir_reap.py


def _delete_internal(o: ce.Obj) -> bool:
    if o.kind == "llvm-cov":
        return _head_cache._evict(o.key, ce.COV_MARKER, _head_cache._rmtree)
    head, name = os.path.split(o.key)
    tomb = os.path.join(head, f".evict-{name}-{os.getpid()}")
    try:
        with _locks_held(o.key):
            os.rename(o.key, tomb)
    except OSError:
        return False
    shutil.rmtree(tomb, ignore_errors=True)
    return True


def _delete_external(o: ce.Obj, root_fd: int) -> bool:
    kfd = cache_move._kind_fd(root_fd, o.kind, create=False)
    try:
        shutil.rmtree(cache_move.slot(o.key), dir_fd=kfd)
        return True
    except OSError:
        return False
    finally:
        os.close(kfd)


def _demote(o: ce.Obj, root_fd: int) -> bool:
    try:
        if o.kind == "llvm-cov" and _head_cache.running(o.key, ce.COV_MARKER):
            return False
        with _locks_held(o.key):
            cache_move.demote(o.key, root_fd, o.kind)
        return True
    except OSError:
        return False


def act(acts: list[ce.Action], root_fd: int | None) -> list[tuple[str, str, bool]]:
    done = []
    for a in acts:
        if a.verb == "demote" and root_fd is not None:
            ok = _demote(a.obj, root_fd)
        elif a.obj.tier == "external" and root_fd is not None:
            ok = _delete_external(a.obj, root_fd)
        else:
            ok = _delete_internal(a.obj)
        done.append((a.verb, a.obj.key, ok))
    return done


def reap_leftovers(build_root: str, root_fd: int | None) -> int:
    """What a killed sweep left: `.evict-*` tombstones beside the build dirs, and `.part` copies on
    the large tier. Only a sweep makes either, and this one holds the sweep lock: all are stale."""
    gone = 0
    for tomb in glob.glob(os.path.join(glob.escape(build_root), "*", ".evict-*")):
        shutil.rmtree(tomb, ignore_errors=True)
        gone += 1
    for kind in sorted(ce.MOVABLE) if root_fd is not None else []:
        try:
            kfd = cache_move._kind_fd(root_fd, kind, create=False)
        except OSError:
            continue
        try:
            for name in [n for n in os.listdir(kfd) if n.endswith(".part")]:
                cache_move._rm_at(kfd, name)
                gone += 1
        finally:
            os.close(kfd)
    return gone


def _sccache_want(verdict: cache_tier.Verdict, root_fd: int | None, internal: str, execute: bool):
    """The dir sccache should serve: `<root>/sccache` made through the verified fd, or internal."""
    if root_fd is None:
        return internal
    if execute:
        with contextlib.suppress(FileExistsError):
            os.mkdir(sccache_repoint.SUBDIR, 0o755, dir_fd=root_fd)
    return os.path.join(verdict.root, sccache_repoint.SUBDIR)


def sweep(roots: Roots, verdict: cache_tier.Verdict, state: str, now: float, execute: bool,
          opener=cache_tier.open_root, sccache_env: sccache_repoint.Env | None = None,
          sccache_internal: str | None = None) -> dict:  # fmt: skip
    root_fd = opener(verdict) if verdict.external else None
    try:
        if execute:
            reap_leftovers(roots.build, root_fd)
        data = ce.read_ledger(state)
        objs = discover(roots, verdict.root, root_fd, now)
        ce.observe(data, objs)
        held_in = sum(o.bytes for o in objs if o.tier == "internal")
        held_ex = sum(o.bytes for o in objs if o.tier == "external")
        b_in = ce.budget(held_in, roots.free(roots.internal), roots.floor_gib)
        b_ex = None
        if root_fd is not None:
            b_in = min(b_in, int(ce.HOT_GIB * ce.GIB))
            b_ex = ce.ext_budget(held_ex, verdict.free, cache_tier.floor_bytes(verdict.total))
        acts, clock = ce.plan(objs, b_in, b_ex, float(data.get("L", 0.0)), now)
        done = act(acts, root_fd) if execute else []
        # THE DRIVE CAME OR WENT: sccache follows within this sweep, no call from anyone
        # (GOH.CACHEEVICT2; lib/sccache_repoint.py `follow` says when a mismatch is left alone).
        internal = sccache_internal or sccache_repoint.internal_dir()
        want = _sccache_want(verdict, root_fd, internal, execute)
        followed, record = sccache_repoint.follow(sccache_env or sccache_repoint.Env(), want,
                                                  data.get("sccache_root"), internal,
                                                  act=execute)  # fmt: skip
        if execute:
            data["L"] = clock
            if record is not None:
                data["sccache_root"] = record
            ce.write_ledger(state, data)
        return {"tier": verdict.reason, "sccache": followed,
                "internal": {"held": held_in, "budget": b_in},
                "external": {"held": held_ex, "budget": b_ex}, "clock": clock,
                "actions": [{"verb": a.verb, "key": a.obj.key, "kind": a.obj.kind,
                             "bytes": a.obj.bytes, "h": a.h} for a in acts],
                "done": [{"verb": v, "key": k, "ok": ok} for v, k, ok in done]}  # fmt: skip
    finally:
        if root_fd is not None:
            os.close(root_fd)


def stamp(path: str, when: float | None = None) -> None:
    """Mark `path` used at `when` (now by default): a goh gate's start, a promote."""
    with open(os.path.join(path, STAMP), "a", encoding="ascii"):
        pass
    os.utime(os.path.join(path, STAMP), None if when is None else (when, when))


def _gib(n: int | None) -> str:
    return "-" if n is None else f"{n / ce.GIB:.1f} GiB"


def main(argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        print(__doc__.rsplit("usage: ", 1)[1].strip())
        return 0
    if argv[:1] == ["stamp"] and len(argv) == 2:
        stamp(argv[1])
        return 0
    verb = argv[0] if argv and argv[0] in ("plan", "run") else "plan"
    state = ce.state_path()
    os.makedirs(os.path.dirname(state), exist_ok=True)
    with open(state + ".lock", "a", encoding="ascii") as lock:
        try:
            lock_order.flock(lock, "cache_sweep", wait=False)
        except BlockingIOError:
            print("cache sweep: another sweep runs")
            return 0
        verdict = cache_tier.resolve(cache_tier.internal_root())
        report = sweep(default_roots(), verdict, state, ce.now(), verb == "run")
    if "--json" in argv:
        print(json.dumps(report, indent=1))
        return 0
    print(f"tier: {report['tier']}")
    print(f"sccache: {report['sccache']}")
    for tier in ("internal", "external"):
        t = report[tier]
        print(f"{tier}: {_gib(t['held'])} held, budget {_gib(t['budget'])}")
    for a in report["actions"]:
        print(f"  {a['verb']:6} {_gib(a['bytes']):>10}  H={a['h']:.3g}  {a['key']}")
    failed = [d for d in report["done"] if not d["ok"]]
    for d in failed:
        print(f"  skipped (held or gone): {d['verb']} {d['key']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
