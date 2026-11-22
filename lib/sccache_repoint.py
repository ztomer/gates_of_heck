#!/usr/bin/env python3
"""Point the sccache server at the cache root the resolver chooses NOW (GOH.CACHEEVICT phase 2).

sccache reads SCCACHE_DIR once, at server start, and 0.19 chains one disk level only
(`cache/multilevel.rs`), so moving between the internal disk and the removable tier
(lib/cache_tier.py) is a restart on the other root: never a path in ~/.zshenv or sccache's config,
which a missing drive would turn into a squatted mount point. When the server's `cache_location`
differs from the resolver's answer this waits for sccache to go idle (compile_requests unchanged
for --idle seconds and no rustc running), stops the server, and starts it again on the new root
from cwd `/` (so the server never holds the drive as its cwd), with no SCCACHE_CACHE_SIZE (the
config file's cap, lib/sccache_cap.py) and never SCCACHE_BASEDIRS (false hits for Rust and, with
preprocessor cache mode, for C: design.md section 3). Clients run with
SCCACHE_IGNORE_SERVER_IO_ERROR=1, so a build caught by the restart compiles locally.

An unplug mid-build costs compiles, never a red gate (probed: 12 cache write errors, exit 0); this
restores caching after it. Stats reset with each restart.

usage: sccache_repoint.py check                  exit 1 (and the command to run) when it differs
       sccache_repoint.py apply [--idle S] [--timeout S]
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
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cache_tier  # lib/, beside this file

SUBDIR = "sccache"
IDLE_S = 30.0
TIMEOUT_S = 1800.0
POLL_S = 5.0
DROP = ("SCCACHE_CACHE_SIZE", "SCCACHE_BASEDIRS", "SCCACHE_DIR")
_LOC = re.compile(r'^Local disk: "(.*)"$')


def internal_dir(environ: dict[str, str] | None = None) -> str:
    """The internal sccache root: lib/sccache_dir.sh's per-OS default."""
    env = os.environ if environ is None else environ
    home = env.get("HOME") or os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Caches", "Mozilla.sccache")
    return os.path.join(env.get("XDG_CACHE_HOME") or os.path.join(home, ".cache"), "sccache")


def _rustc_alive() -> bool:
    out = subprocess.run(["pgrep", "-x", "rustc"], capture_output=True, check=False,
                         stdin=subprocess.DEVNULL)  # fmt: skip
    return out.returncode == 0


@dataclass
class Env:
    """The seams a test fakes: the sccache binary, the clock, sleep, the rustc probe."""

    sccache: str = "sccache"
    clock: object = time.monotonic
    sleep: object = time.sleep
    rustc_alive: object = _rustc_alive
    base: dict = field(default_factory=lambda: dict(os.environ))

    def run(self, *args: str, sccache_dir: str | None = None, cwd: str | None = None):
        env = {k: v for k, v in self.base.items() if k not in DROP}
        if sccache_dir:
            env["SCCACHE_DIR"] = sccache_dir
        return subprocess.run([self.sccache, *args], env=env, cwd=cwd, capture_output=True,
                              text=True, check=False, timeout=120,
                              stdin=subprocess.DEVNULL)  # fmt: skip


def want_dir(verdict: cache_tier.Verdict, environ: dict[str, str] | None = None) -> str:
    """`<external root>/sccache`, made through the verified root's fd, or the internal root."""
    if not verdict.external:
        return internal_dir(environ)
    fd = cache_tier.open_root(verdict)
    try:
        with contextlib.suppress(FileExistsError):
            os.mkdir(SUBDIR, 0o755, dir_fd=fd)
    finally:
        os.close(fd)
    return os.path.join(verdict.root, SUBDIR)


def stats(env: Env, want: str) -> dict | None:
    """The server's stats; a server that was not running starts here, on `want`."""
    out = env.run("--show-stats", "--stats-format", "json", sccache_dir=want)
    try:
        return json.loads(out.stdout) if out.returncode == 0 else None
    except ValueError:
        return None


def location(st: dict | None) -> str | None:
    m = _LOC.match(str((st or {}).get("cache_location", "")))
    return os.path.normpath(m.group(1)) if m else None


def _requests(st: dict | None) -> int | None:
    return ((st or {}).get("stats") or {}).get("compile_requests")


def wait_idle(env: Env, want: str, idle_s: float, timeout_s: float) -> bool:
    """True once compile_requests stays put for `idle_s` with no rustc running; False at timeout."""
    start = quiet = env.clock()
    last = _requests(stats(env, want))
    while env.clock() - start < timeout_s:
        env.sleep(min(POLL_S, idle_s))
        now_req = _requests(stats(env, want))
        if now_req != last or env.rustc_alive():
            last, quiet = now_req, env.clock()
        elif env.clock() - quiet >= idle_s:
            return True
    return False


def repoint(env: Env, want: str, idle_s: float = IDLE_S, timeout_s: float = TIMEOUT_S) -> str:
    """'already', 'repointed', 'busy' (never went idle) or 'failed: <why>'."""
    st = stats(env, want)
    if location(st) == os.path.normpath(want):
        return "already"
    if not wait_idle(env, want, idle_s, timeout_s):
        return "busy"
    env.run("--stop-server")
    started = env.run("--start-server", sccache_dir=want, cwd="/")
    got = location(stats(env, want))
    if got != os.path.normpath(want):
        return f"failed: serving {got} after the restart ({started.stderr.strip()[:200]})"
    return "repointed"


def main(argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        print(__doc__.rsplit("usage: ", 1)[1].strip())
        return 0
    verdict = cache_tier.resolve(cache_tier.internal_root())
    env = Env()
    want = want_dir(verdict) if argv[:1] == ["apply"] else (
        os.path.join(verdict.root, SUBDIR) if verdict.external else internal_dir())  # fmt: skip
    tier = "external" if verdict.external else "internal"
    if argv[:1] == ["check"]:
        got = location(stats(env, want))
        if got == os.path.normpath(want):
            print(f"sccache: serving {got} ({tier}: {verdict.reason})")
            return 0
        print(f"sccache: serving {got}; the resolver wants {want} ({tier}: {verdict.reason})")
        print(f"sccache: run `python3 {os.path.abspath(__file__)} apply`")
        return 1
    if argv[:1] == ["apply"]:
        idle = float(argv[argv.index("--idle") + 1]) if "--idle" in argv else IDLE_S
        cap = float(argv[argv.index("--timeout") + 1]) if "--timeout" in argv else TIMEOUT_S
        verdict_s = repoint(env, want, idle, cap)
        print(f"sccache: {verdict_s}: {want} ({tier}: {verdict.reason})")
        return 0 if verdict_s in ("already", "repointed") else (3 if verdict_s == "busy" else 1)
    print(__doc__.rsplit("usage: ", 1)[1].strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
