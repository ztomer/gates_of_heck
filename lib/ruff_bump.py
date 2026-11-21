"""lib/ruff_bump.py -- a ruff on the box newer than goh's pin starts its own bump-only land
(GOH.RUFFBUMPLAND).

GOH.RUFFCURRENCY made every goh land carry the pin bump (gates/ruff_currency.py --bump-tree, run by
tools/land.sh) once the box runs a newer ruff. Nothing started a land when no goh work was queued,
so from the moment an updater moved ruff every consumer's gate (gates/_py.sh goh_py_ruff_pinned)
stayed red until somebody's next goh land, hours later. goh owns the pin, so goh starts the land:

    run(q, live)   when the newest ruff on the box is newer than the pin on LIVE's main tip, no
                   land is pending, running or in its pre-step, no goh ship is in flight, and
                   this release was never bumped from here: claim the release
                   (`<q>/ruff-bump/<ver>.json`, written BEFORE anything else, so a release is
                   tried at most once, whatever follows), check out main's tip on a fresh branch
                   in `<q>/ruff-bump/wt`, commit the bump there alone (ruff_currency.bump_tree,
                   the bytes every land's bump makes) and enqueue that worktree through the
                   normal queue (`goh land --enqueue --land`, its pre-step and its gate), as
                   item GOH.RUFF<digits>.

WHO CALLS IT. The lander's idle pass, after the queue drained (tools/landq.py drain), and the red
itself: goh_py_ruff_pinned, finding a ruff NEWER than the pin, starts `goh land --ruff-bump
--detach` before it refuses, so the first consumer gate that meets the release starts the land
that clears it. Both are cheap when nothing is owed (a version read per ruff, one file stat).
Off with GOH_LANDQ_RUFF_BUMP=off, and inside a goh suite (lib/idle_suite.py IN_SUITE).

Pinned by tests/test_ruff_bump.py.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(1, str(_HERE.parent / "gates"))
import idle_suite  # noqa: E402 -- lib/: main_checkout, pending, enqueuing, IN_SUITE
import lock_order  # noqa: E402 -- lib/, beside this file
import pid_owner  # noqa: E402 -- lib/, beside this file
import ruff_currency  # noqa: E402 -- gates/: the pin, the box's newest ruff, the bump commit
from full_run import _git  # noqa: E402 -- lib/, beside this file

OFF_ENV = "GOH_LANDQ_RUFF_BUMP"  # "off": nothing starts a bump-only land
STATE = "ruff-bump"
SHIP_NAME = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]+\.json$")  # a ship result, not its side files

Enqueue = Callable[[Path, str], tuple[int, str]]


def ship_dir() -> Path:
    return Path(os.environ.get("GOH_SHIP_DIR") or Path.home() / ".local/state/goh/ship")


def _alive(rec: dict) -> bool:
    """The ship's worker still runs, by the start time beside its pid (GOH.STRAYSIGNAL2): a pid
    handed to another process once the worker ended reads as gone."""
    return pid_owner.alive_line(str(rec.get("pid_stamp") or rec.get("pid")))


def ships_in_flight(repo: str) -> list[str]:
    """The ship results of REPO still running: status `running` and their worker alive (one that
    has not said its pid yet counts as running)."""
    out = []
    for p in sorted((ship_dir() / repo).glob("*.json")):
        if not SHIP_NAME.match(p.name):
            continue
        try:
            rec = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if rec.get("status") == "running" and (not rec.get("pid") or _alive(rec)):
            out.append(p.name)
    return out


def pin_at_tip(main: Path) -> str | None:
    """The ruff pin of MAIN's HEAD (git's copy, never the working tree)."""
    done = _git(main, "show", f"HEAD:{ruff_currency.MANIFEST}")
    return ruff_currency.pin_of(done.stdout) if done.returncode == 0 else None


def default_enqueue(wt: Path, item: str) -> tuple[int, str]:
    """`goh land --enqueue --land WT --item ITEM`: the normal queue, its pre-step and its lander;
    `--foreground`, so its exit is the pre-step's verdict (GOH.TOOLTIMEOUT backgrounds it else)."""
    argv = [sys.executable, str(_HERE.parent / "tools" / "landq.py"), "--enqueue", "--foreground",
            "--land", str(wt), "--item", item]  # fmt: skip
    done = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          check=False, timeout=3600)  # fmt: skip
    said = " | ".join((done.stdout + done.stderr).strip().splitlines()[-2:])
    return done.returncode, said


def _write(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data) + "\n")
    tmp.replace(path)


def _fresh_worktree(main: Path, wt: Path, branch: str, sha: str) -> str:
    """WT a new worktree of MAIN at SHA on BRANCH (any earlier one removed); "" or why not."""
    if wt.exists():
        _git(main, "worktree", "remove", "--force", str(wt), write=True)
    _git(main, "worktree", "prune", write=True)
    wt.parent.mkdir(parents=True, exist_ok=True)
    done = _git(main, "worktree", "add", "-q", "-B", branch, str(wt), sha, write=True)
    return "" if done.returncode == 0 else f"git worktree add: {done.stderr.strip()[:300]}"


def off() -> bool:
    """Off (GOH_LANDQ_RUFF_BUMP=off), or inside a goh suite: a test never starts a real land."""
    return os.environ.get(OFF_ENV) == "off" or any(os.environ.get(k) for k in idle_suite.IN_SUITE)


def look(home: Callable[[], Path], live: Path, detach: bool, landq: Path,
         say: Callable[[str], None] = print) -> int:  # fmt: skip
    """`goh land --ruff-bump`: run() under the queue dir HOME() makes; DETACH returns at once and
    looks in a session of its own (a gate's red starts it, then exits)."""
    if off():
        return 0
    q = home()
    if detach:
        with (q / "lander.log").open("a") as out:
            # A session of its own holds none of its starter's locks (a gate's, under a land).
            env = {k: v for k, v in os.environ.items() if k != "GOH_HELD_LOCKS"}
            subprocess.Popen([sys.executable, str(landq), "--ruff-bump"], stdin=subprocess.DEVNULL,
                             stdout=out, stderr=out, start_new_session=True, env=env)  # fmt: skip
        return 0
    outcome = run(q, live, say)
    say(f"ruff bump: {outcome}")
    return 1 if outcome == "failed" else 0


def run(
    q: Path,
    live: Path,
    say: Callable[[str], None] = print,
    enqueue: Enqueue = default_enqueue,
) -> str:
    """One look, and the land when one is owed; its outcome: off, nothing (not goh's tree, or the
    box is not ahead), done-before (this release was bumped from here already), busy (a land or a
    ship is in flight: the next look decides), enqueued, or failed (said, and recorded)."""
    if off():
        return "off"
    py = os.environ.get("GOH_PY") or sys.executable  # the box as the lander sees it
    path = os.environ.get("PATH", "")
    main = idle_suite.main_checkout(live)
    pin = pin_at_tip(main) if main else None
    new = ruff_currency.ahead(pin, py, path) if main and pin else None
    if not main or not new:
        return "nothing"
    state = q / STATE
    ledger = state / f"{new}.json"
    if ledger.exists():
        return "done-before"
    if idle_suite.pending(q) or idle_suite.enqueuing(q) or ships_in_flight(main.name):
        return "busy"
    state.mkdir(parents=True, exist_ok=True)
    fd = os.open(state / "lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            lock_order.flock(fd, "ruff_bump", wait=False)  # never waits: one bump at a time
        except BlockingIOError:
            return "busy"
        if ledger.exists():  # claimed by the holder before us
            return "done-before"
        return _claimed(q, main, ledger, pin or "", new, say, enqueue, py, path)
    finally:
        os.close(fd)


def _claimed(q: Path, main: Path, ledger: Path, pin: str, new: str, say: Callable[[str], None],
             enqueue: Enqueue, py: str, path: str) -> str:  # fmt: skip
    item = ruff_currency.item(new)
    rec = {"version": new, "pin": pin, "item": item, "state": "claimed",
           "at": dt.datetime.now(dt.UTC).isoformat()}  # fmt: skip
    _write(ledger, rec)  # first: whatever happens next, this release is never tried again

    def end(state: str, why: str = "") -> str:
        _write(ledger, {**rec, "state": state, **({"why": why} if why else {})})
        say(f"ruff bump {new}: {state}{': ' + why if why else ''}")
        return "failed" if state == "failed" else state

    sha = _git(main, "rev-parse", "HEAD").stdout.strip()
    wt = q / STATE / "wt"
    say(f"ruff {new} is on this box and main {sha[:8]} pins {pin}: a bump-only land ({item})")
    if why := _fresh_worktree(main, wt, f"ruff-bump/{new}", sha):
        return end("failed", why)
    rc = ruff_currency.bump_tree(wt, py, path)
    if rc != 0 or _git(wt, "rev-parse", "HEAD").stdout.strip() == sha:
        return end("failed", f"the bump was not committed in {wt} (ruff_currency exit {rc})")
    code, said = enqueue(wt, item)
    if code != 0:
        return end("failed", f"goh land --enqueue exit {code}: {said}")
    return end("enqueued")
