"""check_estate_corpus's memory of VERIFIED entries (roadmap 2.4). A library; never run.

Materialising a corpus is ~4000 file creations, 3.35 of the sweep's 4.6 CPU-s in the kernel -- the
work that serialized `structural --full` across sessions (2026-10-06, tools/session_bench.py) --
for a verdict that changes only when an input does. An entry's record is keyed on EVERYTHING its
verdict depends on, and pinned way by way in tests/test_estate_corpus_cache.py:

* the entry itself (checker, scope, plant, args);
* every corpus file's path, size and mtime_ns -- the instrument lib/tree_stamp.py uses, so an
  edit, a new file or a removed one is a miss;
* `identity()`: the checker's own source, the native binary (path, size, mtime_ns) it runs, and
  every GOH_* the checkers read -- GOH_DIR by the CONTENT of the gates there (lib/gate_identity.py
  `identity`, the formula every proven step keys a gates root by), never its path: goh's land runs
  the tip's head export, ~/.cache/goh/head/<sha>, a new path per commit, and keyed by it (and by
  the export's bin/goh, a launcher copied fresh into each) no land found the last one's records
  (GOH.PROVENPYPATH).

AUDITED (GOH.REUSEAUDIT). A hit is a reuse of the path `estate` (lib/reuse.py): `reusable` asks the
audit first, and one hit in ten is judged afresh anyway and compared (`shadowed`); a path the audit
turned off is never reused.

Only a VERIFIED entry is recorded; "blind" and "unavailable" are asked again. A record older than
TTL_S, or one that does not parse, is a miss. GOH_PROVEN=0 or GOH_ESTATE_CACHE=off (or 0, false, no) turns it off;
GOH_ESTATE_CACHE=<absolute dir> moves it; a relative one turns it off (default ~/.cache/goh/estate-corpus).

`check_estate_corpus.py`'s memory of VERIFIED entries, keyed on the entry, every corpus file's
path/size/mtime, the checker's source, the native binary and the GOH_* environment; red and
unavailable entries are never recorded.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

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


# The words a caller means "off" by. `GOH_ESTATE_CACHE=0` once read as the directory `0`, and a gate run
# in this repo committed its verdicts under `0/`.
OFF_WORDS = ("0", "off", "false", "no")


def cache_dir() -> str | None:
    """The cache's directory, or None when it is off. A relative path is refused (None): it names
    whatever directory the gate happens to run in, which is a consumer's tree."""
    where = os.environ.get("GOH_ESTATE_CACHE", "").strip()
    if where.lower() in OFF_WORDS or os.environ.get("GOH_PROVEN") == "0":
        return None
    if where and not os.path.isabs(where):
        return None
    return where or os.path.join(os.path.expanduser("~"), ".cache", "goh", "estate-corpus")


def identity(source: bytes, env: dict, binary: tuple, goh: str = "") -> str:
    """The checker side of a key: its source, its GOH_* configuration, its native binary, the
    gates that run it (`goh`, their content: goh_identity)."""
    h = hashlib.sha256(b"estate-corpus v2\n")
    h.update(source)
    for k in sorted(env):
        h.update(f"\n{k}={env[k]}".encode())
    h.update(f"\nbinary {binary!r}".encode())
    h.update(f"\ngoh {goh}".encode())
    return h.hexdigest()


_IDENTITY_OF: dict = {}


def goh_identity(goh_dir: str) -> str:
    """The gates at goh_dir as lib/gate_identity.py names a gates root: their content, the binary's
    build inputs included (what an export's bin/goh runs is that content's build). Read once per
    process, as the source is. The formula is the one beside this file, else GOH_DIR's (the
    empty-scope sweep's skeleton has no lib/); with neither, the path, which misses more and is
    never wrong."""
    if goh_dir not in _IDENTITY_OF:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        found = f"path {goh_dir}"
        for root in (here, goh_dir):
            lib = os.path.join(root, "lib", "gate_identity.py") if root else ""
            if lib and os.path.isfile(lib):
                spec = importlib.util.spec_from_file_location("_estate_gate_identity", lib)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                found = mod.identity(Path(goh_dir).resolve())
                break
        _IDENTITY_OF[goh_dir] = found
    return _IDENTITY_OF[goh_dir]


_RESOLVED: dict = {}


def _binary(goh_dir: str) -> tuple:
    """The native binary the checkers run, as goh.sh resolves it: (path, size, mtime_ns). The
    resolution is remembered per (GOH_DIR, GOH_BIN, GOH_LIVE) -- what chooses the binary -- and
    the stat is taken every time, so a rebuilt binary is a new identity."""
    chooser = (goh_dir, os.environ.get("GOH_BIN", ""), os.environ.get("GOH_LIVE", ""))
    if chooser not in _RESOLVED:
        script = '. "$1/gates/_goh_bin.sh"; goh_resolve_native; printf %s "$goh_native"'
        _RESOLVED[chooser] = subprocess.run(
            ["bash", "-c", script, "_", goh_dir],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
        ).stdout.strip()
    path = _RESOLVED[chooser]
    if _export_launcher(path, goh_dir):  # runs the content's build: goh_identity keys it
        return ("export bin/goh",)
    try:
        st = os.stat(path)
    except OSError:
        return (path, None, None)
    return (path, st.st_size, st.st_mtime_ns)


def _export_launcher(path: str, goh_dir: str) -> bool:
    """path is a head export's bin/goh (gates/_export_bin_goh.sh, copied into every export): the
    native build of the export's commit, so the export's content, never the copy's path or mtime."""
    top = os.path.realpath(goh_dir) if goh_dir else ""
    return (
        bool(top)
        and os.path.isfile(os.path.join(top, ".goh-head"))
        and os.path.realpath(path) == os.path.join(top, "bin", "goh")
    )


_SOURCE: dict = {}


def current_identity(source_path: str, goh_dir: str) -> str:
    """identity() of this run: the source read once, the environment and the binary every call."""
    if source_path not in _SOURCE:
        with open(source_path, "rb") as handle:
            _SOURCE[source_path] = handle.read()
    env = {k: v for k, v in os.environ.items() if k.startswith("GOH_")}
    free = set(_verdict_free()) | {"GOH_DIR"}  # by content: goh_identity
    env = {k: v for k, v in env.items() if k not in free}
    return identity(_SOURCE[source_path], env, _binary(goh_dir), goh_identity(goh_dir))


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


_AUDIT: list = []
_SHADOW: dict = {}  # checker -> the estate fields of the hit this run re-judges
_FRESH: set = set()  # checkers re-judged for a disagreement's second run: never a reuse


def _audit():
    """lib/reuse_audit.py of this checkout, else GOH_DIR's; None outside any (the skeleton)."""
    if not _AUDIT:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        found = None
        for root in (here, os.environ.get("GOH_DIR", "")):
            lib = os.path.join(root, "lib", "reuse_audit.py") if root else ""
            if lib and os.path.isfile(lib):
                import sys

                sys.path.insert(0, os.path.dirname(lib))
                found = importlib.import_module("reuse_audit")
                break
        _AUDIT.append(found)
    return _AUDIT[0]


def reusable(key: str, ident: str, entry: dict) -> dict | None:
    """The record to reuse, or None: a miss, a path the audit turned off, or a sampled hit (the
    entry is then judged afresh and `shadowed` compares the two)."""
    seen = None if entry["checker"] in _FRESH else lookup(key)
    audit = _audit()
    if seen is None or audit is None:
        return seen
    fields = {"identity": ident, "entry": entry["checker"], "corpus": key}
    decided = audit.use("estate", fields)
    if decided == "shadow":
        _SHADOW[entry["checker"]] = (key, fields)
    return seen if decided == "reuse" else None


def shadowed(checker: str, fresh: str, again) -> str:
    """The fresh verdict; a sampled hit's is compared with the reused "verified" first."""
    if checker not in _SHADOW:
        return fresh
    key, fields = _SHADOW.pop(checker)

    def both() -> tuple[str, str]:
        _FRESH.add(checker)
        try:
            return ("verified" if lookup(key) else "absent"), again()
        finally:
            _FRESH.discard(checker)

    _audit().compare("estate", fields, "verified", fresh, both)
    return fresh


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
