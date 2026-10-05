"""crates.io lookups: one per crate NAME per TTL, shared on disk, concurrent.

Split out of `check_dep_currency.py` for the file-length cap, and because
NETWORK ACCESS is a distinct concern from deciding what a finding is: this
module can fail, and what it does when it cannot reach is the checker's
decision rather than its own.

`None` means UNREACHED, never "no newer version". That distinction is the
whole reason this is a function rather than an inline expression: a currency
check that treats an unreachable index as a current version converts an
absence of evidence into a clean bill. So an unreached answer is NEVER cached.

THE CACHE (BACKLOG P1b). Measured 2026-10-05: this lookup cost 108 job-seconds
of one media_server push -- one serial, uncached HTTPS request per dependency,
repeated in each of 29 crates. Answers now live one file per crate name under
`$GOH_CRATES_IO_CACHE` (default `~/.cache/goh/crates-io`; `off` disables) for
`GOH_CRATES_IO_TTL_S` seconds (default 21600, 6 h), shared by every crate and
every repo, and `latest_many` asks for the misses concurrently. Only the
report-only arm reads it: the fatal arm (a pin below the graph) is offline and
never calls here. Under `--strict`/`--ratchet` a release newer than the TTL can
be missed for up to the TTL, which is the stated price -- crates.io itself is
not read at the instant of the push either way.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

USER_AGENT = "gates_of_heck-dep-currency"
NET_TIMEOUT = 10
DEFAULT_TTL_S = 6 * 3600
WORKERS = 8
_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # a crates.io name; anything else is not cached


def _fetch(name: str) -> str | None:
    """The network: crates.io's latest stable version of `name`, or None if unreached."""
    url = f"https://crates.io/api/v1/crates/{name}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp:
            doc = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    return (doc.get("crate") or {}).get("max_stable_version")


def _cache_dir() -> str | None:
    where = os.environ.get("GOH_CRATES_IO_CACHE", "")
    if where == "off":
        return None
    if where:
        return where
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "goh", "crates-io")


def _ttl() -> int:
    try:
        return max(0, int(os.environ.get("GOH_CRATES_IO_TTL_S", DEFAULT_TTL_S)))
    except ValueError:
        return DEFAULT_TTL_S


def _cached(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        if time.time() - float(doc["fetched"]) < _ttl() and doc["version"]:
            return str(doc["version"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _store(directory: str, path: str, version: str) -> None:
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"fetched": time.time(), "version": version}, fh)
        os.replace(tmp, path)  # atomic: a concurrent reader sees the old file or the new one
    except OSError:
        pass  # an unwritable cache costs a lookup next time, never a verdict


def latest_stable(name: str) -> str | None:
    """Latest stable version of `name`: from the cache within the TTL, else asked."""
    directory = _cache_dir()
    path = os.path.join(directory, f"{name}.json") if directory and _SAFE.match(name) else None
    if path:
        hit = _cached(path)
        if hit:
            return hit
    version = _fetch(name)
    if version and path:
        _store(directory, path, version)
    return version


def latest_many(names: list[str]) -> dict[str, str | None]:
    """`latest_stable` for every name, the misses asked concurrently."""
    unique = sorted(set(names))
    if not unique:
        return {}
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(unique))) as pool:
        return dict(zip(unique, pool.map(latest_stable, unique)))
