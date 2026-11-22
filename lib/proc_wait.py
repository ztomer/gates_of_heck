"""`goh wait KIND ...`: block until one named thing is true, bounded (GOH.WAITSTOP).

THE CLASS. 114 of the Bash guard's refusals in a week were hand-written waits that could never end:
`until ! pgrep -f "goh land --wait X"; do sleep 20; done`, whose pattern sits in the waiting shell's
own command line (`lib/self_match.py`). A wait names the THING it waits on, never a pattern:

  goh wait pid N [N...]              each process gone (`pid_owner.gone`: a zombie is gone, an
                                     exiting process `ps` still lists is not yet); N may also be a
                                     stamp (`<pid> <start>`) or a file holding one
  goh wait file PATH                 PATH exists
  goh wait json PATH KEY=VALUE[,..]  PATH is whole JSON whose dotted KEYs hold the VALUEs (a string
                                     compares as written, anything else as its JSON: rc=0 ok=true)
  goh wait land ITEM                 ITEM's land finished (`goh land --wait`, its exit passed on)
  goh wait ship RECORD               the ship result RECORD is no longer running (its marks printed)

Every kind takes `--timeout S` (default 7200: no agent outlives about two hours).
Exit: 0 met; 1 the land or ship finished red; 2 usage or an unreadable target; 3 timed out;
4 the ship's worker is gone while its record says running (`goh ship --status`'s codes).
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent))
import pid_owner

from tui.lib import err, ok

LIB = Path(__file__).resolve().parent

DEFAULT_TIMEOUT_S = 7200.0
POLL_S = 0.2
TIMED_OUT = 3


class Usage(Exception):
    """An argument `goh wait` cannot read."""


def _stamp(target: str) -> pid_owner.Stamp | None:
    text = target
    if not target.isdigit() and Path(target).is_file():
        text = Path(target).read_text(encoding="utf-8", errors="replace")
    if not text.split() or not text.split()[0].isdigit():
        raise Usage(f"{target!r} is neither a pid, a stamp line nor a file holding one")
    return pid_owner.from_line(text)


def pids_gone(targets: list[str]) -> Callable[[], bool]:
    """True once every process the targets name is gone (stamped NOW, so a number reused later
    reads as gone, never as the process waited on)."""
    stamps = [s for s in map(_stamp, targets) if s is not None]
    return lambda: all(pid_owner.gone(s) for s in stamps)


def conditions(specs: list[str]) -> list[tuple[list[str], str]]:
    """`a.b=x,c=1` (one argument or several) as ([path], value) pairs."""
    out = []
    for spec in specs:
        for part in filter(None, spec.split(",")):
            key, eq, value = part.partition("=")
            if not eq or not key:
                raise Usage(f"{part!r} is not KEY=VALUE")
            out.append((key.split("."), value))
    if not out:
        raise Usage("json needs at least one KEY=VALUE")
    return out


def holds(doc: object, path: list[str], value: str) -> bool:
    """Does DOC hold VALUE at the dotted PATH (a list index is a number)?"""
    for key in path:
        if isinstance(doc, dict) and key in doc:
            doc = doc[key]
        elif isinstance(doc, list) and key.isdigit() and int(key) < len(doc):
            doc = doc[int(key)]
        else:
            return False
    return doc == value if isinstance(doc, str) else json.dumps(doc) == value


def json_holds(path: Path, wanted: list[tuple[list[str], str]]) -> Callable[[], bool]:
    """True once PATH reads as whole JSON holding every wanted value (a torn write reads again)."""

    def check() -> bool:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return all(holds(doc, keys, value) for keys, value in wanted)

    return check


def ship_finished(record: Path) -> Callable[[], bool]:
    """True once the ship RECORD says anything but running, or says running with its worker gone."""

    def check() -> bool:
        try:
            res = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if res.get("status") not in ("running", "bisecting"):  # a bisecting worker runs on
            return True
        pid = res.get("pid")
        return bool(pid) and not pid_owner.alive_line(str(res.get("pid_stamp") or pid))

    return check


def until(check: Callable[[], bool], timeout: float, what: str) -> int:
    """Poll CHECK until true (0) or TIMEOUT seconds pass (3)."""
    end = time.monotonic() + timeout
    pause = POLL_S
    while not check():
        if time.monotonic() >= end:
            err(f"goh wait: {what} is still not so after {timeout:.0f}s")
            return TIMED_OUT
        time.sleep(min(pause, max(0.0, end - time.monotonic())))
        pause = min(pause * 1.5, 5.0)  # quick for a short wait, light for a long one
    return 0


def run(kind: str, args: list[str], timeout: float, given: bool) -> int:
    if kind == "land":
        if len(args) != 1:
            raise Usage("land takes one ITEM")
        cmd = [sys.executable, str(LIB.parent / "tools" / "landq.py"), "--wait", args[0]]
        return subprocess.run(cmd + (["--timeout", str(timeout)] if given else []),
                              stdin=subprocess.DEVNULL, check=False).returncode  # fmt: skip
    if kind == "pid":
        if not args:
            raise Usage("pid takes at least one N")
        return until(pids_gone(args), timeout, f"pid {' '.join(args)} gone")
    if kind == "file":
        if len(args) != 1:
            raise Usage("file takes one PATH")
        return until(Path(args[0]).exists, timeout, f"{args[0]} there")
    if kind == "json":
        if len(args) < 2:
            raise Usage("json takes PATH KEY=VALUE[,KEY=VALUE...]")
        what = f"{args[0]} holding {','.join(args[1:])}"
        return until(json_holds(Path(args[0]), conditions(args[1:])), timeout, what)
    if kind == "ship":
        if len(args) != 1:
            raise Usage("ship takes one RECORD")
        if not Path(args[0]).is_file():
            raise Usage(f"no ship record at {args[0]}")
        rc = until(ship_finished(Path(args[0])), timeout, f"the ship {args[0]} finished")
        if rc:
            return rc
        import ship_status  # its marks and its exit code, exactly as `goh ship --status`

        return ship_status.status(args[0])
    raise Usage(f"no wait kind {kind!r}: pid, file, json, land or ship")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goh wait", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 2)[2])  # fmt: skip
    ap.add_argument("kind", choices=["pid", "file", "json", "land", "ship"])
    ap.add_argument("args", nargs="+")
    ap.add_argument("--timeout", type=float, default=None, help="seconds (default 7200)")
    ns = ap.parse_args(argv)
    timeout = DEFAULT_TIMEOUT_S if ns.timeout is None else ns.timeout
    try:
        rc = run(ns.kind, ns.args, timeout, ns.timeout is not None)
    except Usage as e:
        err(f"goh wait {ns.kind}: {e}")
        return 2
    if rc == 0 and ns.kind in ("pid", "file", "json"):
        ok(f"goh wait {ns.kind}: {' '.join(ns.args)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
