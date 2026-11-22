#!/usr/bin/env python3
"""sccache's disk cap, written here and nowhere else (GOH.SCCACHECAP).

sccache evicted before reuse: at its 20 GiB cap the oldest entry was 7.6 h old (median 2.7 h) and
the Rust hit rate 49% (2026-11-15); 17.9% on 2026-11-22 (16204 hit / 74379 miss). The cap was set
by hand in two places that disagree on reach: the config file (every server) and SCCACHE_CACHE_SIZE
in ~/.zshenv (which outranks the file, but only for a server started from zsh). CAP_GIB is the one
value; `apply` writes it into the config file's `[cache.disk] size`, keeping every other line;
`check` exits 1 naming each disagreement, an environment SCCACHE_CACHE_SIZE included (a second
owner). The gates drop an inherited SCCACHE_CACHE_SIZE (lib/sccache_dir.sh), so a server a gate
starts reads the file. A running server keeps the size it started with: `sccache --stop-server`.

Whether 40 GiB stays is GOH.SCCACHECAP's 48 h measurement (verify_after): kept if the Rust hit rate
rises 10 points, else CAP_GIB goes back with the numbers. The VALUE is the one evictor's
(lib/cache_evict.py SCCACHE_GIB, GOH.CACHEEVICT), which counts sccache's bytes in its budget and
leaves sccache's own LRU as the backstop at this cap.

usage: sccache_cap.py apply|check        (SCCACHE_CONF names another config file)
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

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cache_evict  # lib/, beside this file

CAP_GIB = cache_evict.SCCACHE_GIB
CAP_BYTES = CAP_GIB << 30
_SIZE = re.compile(r"^\s*size\s*=\s*(\d+)")
_TABLE = re.compile(r"^\s*\[([^\]]+)\]")


def config_path() -> Path:
    """The file sccache reads: SCCACHE_CONF, else its per-OS default."""
    if conf := os.environ.get("SCCACHE_CONF"):
        return Path(conf)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Mozilla.sccache" / "config"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "sccache" / "config"


def _disk_size(lines: list[str]) -> tuple[int | None, int | None]:
    """(index of the `[cache.disk]` size line, its value), or Nones."""
    table = None
    for i, line in enumerate(lines):
        if m := _TABLE.match(line):
            table = m.group(1).strip()
        elif table == "cache.disk" and (m := _SIZE.match(line)):
            return i, int(m.group(1))
    return None, None


def apply(path: Path) -> None:
    """PATH's `[cache.disk] size` set to CAP_BYTES; every other line kept."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    want = f"size = {CAP_BYTES}   # {CAP_GIB} GiB, set by goh lib/sccache_cap.py"
    at, _ = _disk_size(lines)
    if at is not None:
        lines[at] = want
    else:
        head = next((i for i, ln in enumerate(lines) if ln.strip() == "[cache.disk]"), None)
        if head is None:
            lines += ([""] if lines else []) + ["[cache.disk]", want]
        else:
            lines.insert(head + 1, want)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def check(path: Path) -> list[str]:
    """Each disagreement with CAP_BYTES, said; [] when goh is the only word."""
    found = []
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    _, size = _disk_size(text.splitlines())
    if size != CAP_BYTES:
        found.append(f"{path}: [cache.disk] size is {size}, not {CAP_BYTES} ({CAP_GIB} GiB)")
    if (env := os.environ.get("SCCACHE_CACHE_SIZE")) is not None:
        found.append(f"SCCACHE_CACHE_SIZE={env} in the environment outranks {path}: a second owner")
    return found


if __name__ == "__main__":
    if sys.argv[1:] == ["apply"]:
        apply(config_path())
        print(f"sccache cap: {config_path()} [cache.disk] size = {CAP_GIB} GiB")
        sys.exit(0)
    if sys.argv[1:] == ["check"]:
        problems = check(config_path())
        for p in problems:
            print(f"sccache cap: {p}")
        sys.exit(1 if problems else 0)
    sys.exit("usage: sccache_cap.py apply|check")
