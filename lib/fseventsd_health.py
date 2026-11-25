"""fseventsd_health.py -- one line naming a saturated fseventsd, with its restart (GOH.FSEVCHURN).

THE CASE (2026-11-23). fseventsd sat at 6.9 GB RSS and 105.7% CPU after 2 d 18 h; the suite's fs
churn tests read the stuck daemon only as red or unmeasured, and the owner restarted it by hand (the
fresh one read 10 MB). A stuck fseventsd slows every file event on the machine, so a whole suite
run (tools/pytest.sh) ends by asking this: quiet under the bound, else a `⚠` line on stderr naming
the RSS, the bound and the restart command. Restarting it is the owner's call; this only says so.
Never a `✗`: the suite's own verdict stands, and a `✗` beside exit 0 is refused by the gate
(lib/xexit.py), so one saturated daemon turned a green land red (2026-11-24, GOH.FSEVCHURNX).

WHY NOT A QUIET LOCATION (probe 2026-11-23, 100k files a window, 4 rounds, 40 s windows, fseventsd's
own cputime): no place on this Mac is ignored by fseventsd. Internal $TMPDIR/goh.noindex cost a mean
11.8 s a window, /Volumes/1TB/cache.noindex -- a volume whose .fseventsd/no_log is set -- 11.8 s,
paths under `Backups.backupdb/` and `.MobileBackups/` (names the kernel hides from third-party
FSEvents clients, not from fseventsd) 16.9 and 17.7 s, against idle windows of 5.9 s. With a FRESH
name per window (the shape that once grew it to 11.7 GB), two runs: $TMPDIR 19.5 and 20.9 s, the
no_log volume 14.3 and 16.1 s, idle 11.1 and 8.5 s -- cheaper, never free, and RSS moved under
0.5 MB either way. no_log stops the on-disk log only; every event is still read. Fewer file events
is the only lever.

    python3 lib/fseventsd_health.py      # exit 0 quiet or no daemon, 1 saturated (stderr names it)

GOH_FSEVENTSD_RSS_KB replaces the reading (a number, or `none` for no daemon) for the tests;
GOH_FSEVENTSD_LIMIT_KB moves the bound. Pinned by tests/test_fseventsd_health.py.

GOH.FSEVCHURN: one line after a whole suite (tools/pytest.sh) naming a saturated fseventsd -- RSS
over 1 GB (the stuck daemon read 6.9 GB, 2026-11-23) -- with its restart command, the owner's call.
Its docstring holds the probe that found no location fseventsd ignores on the owner's Mac: a no_log
volume costs the same CPU per event as $TMPDIR.
"""

from __future__ import annotations

import os
import subprocess
import sys

LIMIT_KB = 1024 * 1024  # 1 GB: a fresh daemon reads ~10-15 MB; the stuck one read 6.9 GB
RESTART = "sudo launchctl kickstart -k system/com.apple.fseventsd"


def _gb(kb: int) -> str:
    return f"{kb / (1024 * 1024):.1f} GB"


def verdict(rss_kb: int, limit_kb: int = LIMIT_KB) -> str | None:
    """None when `rss_kb` is under the bound; else the fault line."""
    if rss_kb <= limit_kb:
        return None
    return (
        f"⚠ fseventsd is saturated: {_gb(rss_kb)} RSS, over the {_gb(limit_kb)} bound -- every"
        f" file event on this Mac waits on it. Restart it (the owner's call): {RESTART}"
    )


def rss_kb() -> int | None:
    """fseventsd's RSS in KB, or None when there is no such daemon (Linux, a sandbox)."""
    seam = os.environ.get("GOH_FSEVENTSD_RSS_KB")
    if seam is not None:
        return None if seam == "none" else int(seam)
    try:
        pids = subprocess.run(
            ["pgrep", "-x", "fseventsd"], capture_output=True, text=True, check=False,
            stdin=subprocess.DEVNULL, timeout=10,
        ).stdout.split()  # fmt: skip
        if not pids:
            return None
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", pids[0]], capture_output=True, text=True, check=False,
            stdin=subprocess.DEVNULL, timeout=10,
        ).stdout.strip()  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    return int(out) if out.isdigit() else None


def main() -> int:
    kb = rss_kb()
    if kb is None:
        return 0
    limit = int(os.environ.get("GOH_FSEVENTSD_LIMIT_KB") or LIMIT_KB)
    line = verdict(kb, limit)
    if line is None:
        return 0
    print(line, file=sys.stderr)
    return 1


if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]
    sys.exit(main())
