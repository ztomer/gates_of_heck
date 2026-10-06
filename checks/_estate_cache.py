"""check_estate_corpus's memory of VERIFIED entries (roadmap 2.4). A library; never run.

Materialising a corpus is ~4000 file creations, 3.35 of the sweep's 4.6 CPU-s in the kernel -- the
work that serialized `structural --full` across sessions (2026-10-06, tools/session_bench.py) --
for a verdict that changes only when an input does. An entry's record is keyed on EVERYTHING its
verdict depends on, and pinned way by way in tests/test_estate_corpus_cache.py:

* the entry itself (checker, scope, plant, args);
* every corpus file's path, size and mtime_ns -- the instrument lib/tree_stamp.py uses, so an
  edit, a new file or a removed one is a miss;
* `identity()`: the checker's own source, the native binary (path, size, mtime_ns) it runs, and
  every GOH_* the checkers read.

Only a VERIFIED entry is recorded; "blind" and "unavailable" are asked again. A record older than
TTL_S, or one that does not parse, is a miss. GOH_PROVEN=0 or GOH_ESTATE_CACHE=off turns it off;
GOH_ESTATE_CACHE=<dir> moves it (default ~/.cache/goh/estate-corpus).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time

TTL_S = 7 * 86400


def _verdict_free() -> tuple:
    """GOH_* keys that never change a verdict (gates/verdict_free_keys.txt, the one list), found
    through GOH_DIR first: a copy of this file run elsewhere -- the empty-scope sweep's skeleton --
    has no gates/ beside it, and crashing there read as the sweep's "now FAILS". No list found is
    no exclusions: a key with more in it misses more, and is never wrong."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for root in (os.environ.get("GOH_DIR", ""), here):
        path = os.path.join(root, "gates", "verdict_free_keys.txt") if root else ""
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                return tuple(ln.strip() for ln in handle if ln.strip() and not ln.startswith("#"))
    return ()


def cache_dir() -> str | None:
    where = os.environ.get("GOH_ESTATE_CACHE", "")
    if where == "off" or os.environ.get("GOH_PROVEN") == "0":
        return None
    return where or os.path.join(os.path.expanduser("~"), ".cache", "goh", "estate-corpus")


def identity(source: bytes, env: dict, binary: tuple) -> str:
    """The checker side of a key: its source, its GOH_* configuration, its native binary."""
    h = hashlib.sha256(b"estate-corpus v1\n")
    h.update(source)
    for k in sorted(env):
        h.update(f"\n{k}={env[k]}".encode())
    h.update(f"\nbinary {binary!r}".encode())
    return h.hexdigest()


_RESOLVED: dict = {}


def _binary(goh_dir: str) -> tuple:
    """The native binary the checkers run, as goh.sh resolves it: (path, size, mtime_ns). The
    resolution is remembered per (GOH_DIR, GOH_BIN, GOH_LIVE) -- what chooses the binary -- and
    the stat is taken every time, so a rebuilt binary is a new identity."""
    chooser = (goh_dir, os.environ.get("GOH_BIN", ""), os.environ.get("GOH_LIVE", ""))
    if chooser not in _RESOLVED:
        script = '. "$1/gates/_goh_bin.sh"; goh_resolve_native; printf %s "$goh_native"'
        _RESOLVED[chooser] = subprocess.run(
            ["bash", "-c", script, "_", goh_dir], capture_output=True, text=True, check=False
        ).stdout.strip()
    path = _RESOLVED[chooser]
    try:
        st = os.stat(path)
    except OSError:
        return (path, None, None)
    return (path, st.st_size, st.st_mtime_ns)


_SOURCE: dict = {}


def current_identity(source_path: str, goh_dir: str) -> str:
    """identity() of this run: the source read once, the environment and the binary every call."""
    if source_path not in _SOURCE:
        with open(source_path, "rb") as handle:
            _SOURCE[source_path] = handle.read()
    env = {k: v for k, v in os.environ.items() if k.startswith("GOH_")}
    free = set(_verdict_free())
    env = {k: v for k, v in env.items() if k not in free}
    return identity(_SOURCE[source_path], env, _binary(goh_dir))


def entry_key(ident: str, entry: dict, source_root: str, files: list[str]) -> str:
    h = hashlib.sha256(ident.encode())
    h.update(repr(sorted((k, v) for k, v in entry.items() if k != "why")).encode())
    for rel in files:
        try:
            st = os.stat(os.path.join(source_root, rel), follow_symlinks=False)
            h.update(f"\n{rel}\t{st.st_size}\t{st.st_mtime_ns}".encode())
        except OSError:
            h.update(f"\n{rel}\tmissing".encode())
    return h.hexdigest()


def lookup(key: str) -> dict | None:
    where = cache_dir()
    if where is None:
        return None
    try:
        with open(os.path.join(where, key + ".json"), encoding="utf-8") as handle:
            rec = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("key") != key or rec.get("state") != "verified":
        return None
    if time.time() - float(rec.get("at", 0)) > TTL_S:
        return None
    return rec


def record(key: str, line: str) -> None:
    """Remember a VERIFIED entry. A failed write is dropped: the memory never changes a verdict."""
    where = cache_dir()
    if where is None:
        return
    try:
        os.makedirs(where, exist_ok=True)
        tmp = os.path.join(where, f".{key}.{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"key": key, "state": "verified", "at": time.time(), "line": line}, handle)
        os.replace(tmp, os.path.join(where, key + ".json"))
    except OSError:
        pass
