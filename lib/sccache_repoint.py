#!/usr/bin/env python3
"""Point the sccache server at the cache root the resolver chooses NOW (GOH.CACHEEVICT phase 2).

sccache reads SCCACHE_DIR once, at server start, and 0.19 chains one disk level only
(`cache/multilevel.rs`), so moving between the internal disk and the removable tier
(lib/cache_tier.py) is a restart on the other root: never a path in ~/.zshenv or sccache's config,
which a missing drive would turn into a squatted mount point. When the server's `cache_location`
differs from the resolver's answer this waits for sccache to go idle (compile_requests unchanged
for --idle seconds and no rustc running), stops the server, and starts it again on the new root
from cwd `/` (so the server never holds the drive as its cwd), with SCCACHE_CACHE_SIZE set to
lib/sccache_cap.py's cap (an environment SCCACHE_DIR replaces the config file's disk section, cap
included: GOH.REPOINTSIZE) and never SCCACHE_BASEDIRS (false hits for Rust and, with
preprocessor cache mode, for C: design.md section 3). lib/goh-rustc
exports SCCACHE_IGNORE_SERVER_IO_ERROR=1, so a build caught by the restart compiles locally.

An unplug mid-build costs compiles, never a red gate (probed: 12 cache write errors, exit 0); this
restores caching after it. Stats reset with each restart.

The half-hourly evictor sweep (lib/cache_sweep.py) calls `follow` every run: any difference, a
drive that came or went or a server somebody started on the default dir, is applied within one
sweep (GOH.CACHEEVICT2, GOH.SCCACHEROOTFOLLOW). `start` starts a MISSING server on the resolver's
root: lib/goh-rustc, and the gates' entry through it, call it when none answers.

`goh sccache-repoint check`, `apply` and `start` (GOH.CACHEEVICT): the sccache server follows the
cache root `lib/cache_tier.py` chooses now -- `<large tier>/sccache` with the removable drive
present, the internal per-OS root without it. sccache reads SCCACHE_DIR once and 0.19 chains one
disk level, so a change is a restart: wait until compile_requests holds still for `--idle` s (30)
with no rustc running (`busy`, exit 3, at `--timeout`), `--stop-server`, `--start-server` from cwd
`/` with SCCACHE_DIR set and SCCACHE_CACHE_SIZE / SCCACHE_BASEDIRS dropped; `check` exits 1 and
prints the command when they differ. `start` starts a missing server on that root
(GOH.SCCACHEROOTFOLLOW); `follow`, each sweep's step, applies any difference, past an hour with no
idle window too.

usage: sccache_repoint.py check                  exit 1 (and the command to run) when it differs
       sccache_repoint.py apply [--idle S] [--timeout S]
       sccache_repoint.py start [INTERNAL]    start a missing server; INTERNAL is the fallback dir
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
import pwd
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cache_tier  # lib/, beside this file
import sccache_cap  # lib/, beside this file: the one disk cap

SUBDIR = "sccache"
IDLE_S = 30.0
TIMEOUT_S = 1800.0
POLL_S = 5.0
RESTARTS = 3
DROP = ("SCCACHE_CACHE_SIZE", "SCCACHE_BASEDIRS", "SCCACHE_DIR")
_LOC = re.compile(r'^Local disk: "(.*)"$')


def internal_dir(environ: dict[str, str] | None = None) -> str:
    """The internal sccache root: lib/sccache_dir.sh's per-OS default."""
    env = os.environ if environ is None else environ
    home = env.get("HOME") or os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Caches", "Mozilla.sccache")
    return os.path.join(env.get("XDG_CACHE_HOME") or os.path.join(home, ".cache"), "sccache")


def _alive(name: str) -> bool:
    out = subprocess.run(["pgrep", "-x", name], capture_output=True, check=False,
                         stdin=subprocess.DEVNULL)  # fmt: skip
    return out.returncode == 0


def _rustc_alive() -> bool:
    return _alive("rustc")


def _server_alive() -> bool:
    return _alive("sccache")


@dataclass
class Env:
    """The seams a test fakes: the sccache binary, the clock, sleep, the rustc and server probes."""

    sccache: str = "sccache"
    clock: object = time.monotonic
    sleep: object = time.sleep
    rustc_alive: object = _rustc_alive
    alive: object = _server_alive
    base: dict = field(default_factory=lambda: dict(os.environ))

    def run(self, *args: str, sccache_dir: str | None = None, cwd: str | None = None):
        env = {k: v for k, v in self.base.items() if k not in DROP}
        if sccache_dir:
            env["SCCACHE_DIR"] = sccache_dir
            # SCCACHE_DIR makes sccache build its disk config from the environment, which replaces
            # the config file's [cache.disk] section, cap included: without this the server ran at
            # the 10 GiB default (GOH.REPOINTSIZE, seen live 2026-11-23). goh's one cap, never the
            # caller's own SCCACHE_CACHE_SIZE (dropped above).
            env["SCCACHE_CACHE_SIZE"] = f"{sccache_cap.CAP_GIB}G"
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


def repoint(env: Env, want: str, idle_s: float = IDLE_S, timeout_s: float = TIMEOUT_S,
            force: bool = False) -> str:  # fmt: skip
    """'already', 'repointed', 'busy' (never went idle; FORCE restarts it then) or 'failed: <why>'.
    A plain client can start a server on its own dir between the stop and the start: retried."""
    st = stats(env, want)
    if location(st) == os.path.normpath(want):
        return "already"
    if not wait_idle(env, want, idle_s, timeout_s) and not force:
        return "busy"
    for _try in range(RESTARTS):
        env.run("--stop-server")
        started = env.run("--start-server", sccache_dir=want, cwd="/")
        got = location(stats(env, want))
        if got == os.path.normpath(want):
            return "repointed"
    return f"failed: serving {got} after the restart ({started.stderr.strip()[:200]})"


FOLLOW_TIMEOUT_S = 300.0  # a sweep waits this long for quiet, then retries at the next sweep
FORCE_AFTER_S = 3600.0  # owed this long with no idle window: the restart waits no more


def served(env: Env, want: str) -> str | None:
    """The dir a RUNNING server serves; None with no server (the stats call would start one)."""
    return location(stats(env, want)) if env.alive() else None


def follow(
    env: Env,
    want: str,
    internal: str,
    now: float,
    owed: float | None,
    hold: dict | None,
    idle_s: float = IDLE_S,
    timeout_s: float = FOLLOW_TIMEOUT_S,
    act: bool = True,
) -> tuple[str, float | None]:
    """One sweep's step (GOH.SCCACHEROOTFOLLOW): (status, since when a move is owed).

    ANY difference between the root served and `want` is applied once the server is idle, and past
    FORCE_AFTER_S of no idle window without one: continuous builds cannot hold it off for ever.
    `hold` ({"until", "why"}, `cache_sweep.py hold`) leaves a difference until it expires (a
    hit-rate window, which a restart's stats reset would void); a server on a root that is neither
    `internal` nor `want` (the drive left) comes home regardless. A server that is not running is
    never started here: the next compile starts it on the resolver's root (`start`)."""
    got = served(env, want)
    if got is None:
        return "no server", None
    home, there = os.path.normpath(internal), os.path.normpath(want)
    if got == there:
        return "already", None
    if hold and now < hold["until"] and got == home:
        left = (hold["until"] - now) / 3600
        return f"held {left:.1f} h more ({hold['why']}): serving {got}, not {want}", None
    owed = now if owed is None else owed
    if not act:
        return f"would re-point: serving {got}, the resolver wants {want}", owed
    said = repoint(env, want, idle_s, timeout_s, force=now - owed >= FORCE_AFTER_S)
    return said, (None if said in ("already", "repointed") else owed)


def fallback_dir(named: str, home: str | None = None) -> str:
    """The internal dir `start` falls back to: NAMED (the gate's pin), but never one under a
    sandbox's HOME, which the server would keep for its life (GOH.CACHEBOUND)."""
    home = home or pwd.getpwuid(os.getuid()).pw_dir
    if named and (sys.platform != "darwin" or named.startswith(home.rstrip("/") + "/")):
        return named
    return internal_dir({**os.environ, "HOME": home})


def start(env: Env, verdict: cache_tier.Verdict, internal: str) -> str:
    """A missing server started on the resolver's root, `internal` when the drive cannot hold it;
    the line that says where it is and why."""
    want, why = internal, verdict.reason
    if verdict.external:
        try:
            want = want_dir(verdict)
        except OSError as e:
            why = f"{verdict.root} left: {e}"
    out = env.run("--start-server", sccache_dir=want, cwd="/")
    got = location(stats(env, want))
    if got is None and want != internal:
        why = f"no server on {want}: {out.stderr.strip()[:200]}"
        env.run("--start-server", sccache_dir=internal, cwd="/")
        got = location(stats(env, internal))
    tier = "external" if want != internal and got == os.path.normpath(want) else "internal"
    return f"sccache: server started, serving {got} ({tier}: {why})"


LOG_MAX = 64 << 10


def say(line: str) -> None:
    """LINE printed, and kept in the start log: the wrapper's own output is the crate's to cargo."""
    print(line)
    path = os.environ.get("GOH_SCCACHE_LOG") or os.path.expanduser(
        "~/.local/state/goh/sccache-start.log")  # fmt: skip
    with contextlib.suppress(OSError):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        big = os.path.exists(path) and os.path.getsize(path) > LOG_MAX
        with open(path, "w" if big else "a", encoding="utf-8") as log:
            log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {line}\n")


def trim(path: str, cap: int) -> int:
    """The least recently used files of the sccache dir PATH deleted until it holds CAP bytes; the
    bytes freed. Only for a dir no server serves (a live one's LRU is the server's)."""
    if os.path.islink(path):
        return 0
    found = []
    for base, _dirs, names in os.walk(path):  # follows no symlink
        for name in names:
            with contextlib.suppress(OSError):
                st = os.lstat(os.path.join(base, name))
                if stat.S_ISREG(st.st_mode):
                    found.append((max(st.st_mtime, st.st_atime), st.st_size, base, name))
    total, freed = sum(f[1] for f in found), 0
    for _used, size, base, name in sorted(found):
        if total - freed <= cap:
            break
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(base, name))
            freed += size
    return freed


def main(argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        print(__doc__.rsplit("usage: ", 1)[1].strip())
        return 0
    verdict = cache_tier.resolve(cache_tier.internal_root())
    env = Env()
    if argv[:1] == ["start"]:
        named = os.environ.get("GOH_RUSTC_NEXT", "")
        if os.path.basename(named) == "sccache":
            env.sccache = named  # the sccache the wrapper is about to run
        say(start(env, verdict, fallback_dir(argv[1] if len(argv) > 1 else "")))
        return 0
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
