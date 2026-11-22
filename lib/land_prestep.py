"""The land pre-step, run at enqueue (GOH.ENQUEUEPRE; tools/landq.py's THE ENQUEUE PRE-STEP).

It is land.sh's own pre-step: GOH_LAND_PRE_ONLY=1 stops the script before the land lock, so the
set (GOH_LAND_PRE), the short lane and the deadline (GOH_LAND_PRE_MAX) are the lander's. A red
set raises Red naming the failing tests, its later lines each failing test's bounded traceback
(lib/land_cause.py `excerpt`, GOH.PRESTEPLOG); a deadline is no verdict (GOH.PRESTEPDEADLINE),
so the tip queues and a line says so. Pinned by tests/test_landq.py.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

affected_verdict = importlib.import_module("affected_verdict")
gitrun = importlib.import_module("gitrun")
land_cause = importlib.import_module("land_cause")
main_red = importlib.import_module("main_red")
killtree = importlib.import_module("killtree")
land_slot = importlib.import_module("land_slot")
land_spd = importlib.import_module("land_spd")
landq_inflight = importlib.import_module("landq_inflight")
state_bounds = importlib.import_module("state_bounds")
tmp_root = importlib.import_module("tmp_root")
_tui = importlib.import_module("tui.lib")
info, warn = _tui.info, _tui.warn

# land.sh bounds the set at its share's deadline (lib/pre_deadline.py: at most GOH_LAND_PRE_CEIL,
# 25 min) after a wait for a short-lane slot.
TIMEOUT = 30 * 60


class Red(Exception):
    pass


# tools/land.sh's one line for a pre-step that found nothing: a deadline ("no verdict after N s at
# load L") or a slot wait that expired ("no verdict: waited N s for a gate slot and ran nothing").
NO_VERDICT = re.compile(r"pre-land: no verdict[: ] ?(.*?) -- the gate decides")


def no_verdict(lines: list[str]) -> dict | None:
    """The pre-step's no-verdict record from its output (GOH.PREWAITVERDICT): `kind` (`slot-wait`
    or `deadline`) and `why`, in words; None when it reached a verdict. Put on the land request as
    `pre_verdict`, for the selection-miss ledger to tell a set that never ran from one that was
    skipped (`--no-pre`) or chose too little."""
    for line in lines:
        if found := NO_VERDICT.search(line):
            why = found.group(1).strip()
            return {"state": "none", "kind": "slot-wait" if "gate slot" in why else "deadline",
                    "why": why}  # fmt: skip
    return None


def took_figures(figures: Path, lines: list[str]) -> dict:
    """The pre-step's wait, run, limit, workers and cpu_s as tools/land.sh wrote them to FIGURES
    in every outcome (GOH.PREWIDTH), the file removed; else the green line's (lib/land_spd.py
    `pre_run`: a land.sh from before the file); {} when neither says."""
    try:
        got = json.loads(figures.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = None
    figures.unlink(missing_ok=True)
    if isinstance(got, dict):
        return {k: v for k, v in got.items() if k in PRE_RUN_KEYS}
    return land_spd.pre_run(lines) or {}


PRE_RUN_KEYS = ("wait_s", "run_s", "limit_s", "workers", "cpu_s")


def skipped(why: str) -> dict:
    """`pre_run` of an enqueue whose pre-step was skipped (`--no-pre WHY`): it ran nothing."""
    return {**dict.fromkeys(PRE_RUN_KEYS, 0), "skipped": why}


def keep_path(q: Path, sha: str) -> Path:
    """Where one enqueue keeps its verdict (GOH.PRELANDTWICE): under the queue's `pre/`."""
    (q / "pre").mkdir(parents=True, exist_ok=True)
    return q / "pre" / f"{sha}-{time.time_ns()}"


SHA = re.compile(r"[0-9a-f]{40}")
PIN = "refs/goh/pre/"  # a rebased pre-step's judged tip, pinned per keep (GOH.PREONTIP)


def adopt(req: dict, keep: Path) -> None:
    """Put a kept green verdict on the request: `keep` names the very sha enqueued, or -- a request
    in a slot, whose pre-step rebased onto main first (GOH.PREONTIP) -- the rebased tip it judged
    and the main below it, `{"judged": P, "base": B}`; anything else is nothing."""
    with contextlib.suppress(OSError):
        words = keep.read_text().split()
        if words == [req["sha"]]:
            req["pre"] = {"judged": req["sha"], "keep": str(keep)}
        elif req.get("slot") and len(words) == 2 and all(SHA.fullmatch(w) for w in words):
            req["pre"] = {"judged": words[0], "base": words[1], "keep": str(keep)}


def _unpin(repo: str | Path, names: list[str]) -> None:
    """The pins of the keeps NAMES removed from REPO (best effort: a pin is only a gc root)."""
    for name in names:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            gitrun.run_writer(("update-ref", "-d", f"{PIN}{name}"), repo=repo, deadline_s=60)


def _pins(repo: str | Path) -> list[str]:
    """The keep names REPO holds a pin for."""
    try:
        got = gitrun.run_reader(("for-each-ref", "--format=%(refname)", PIN), repo=repo, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln.removeprefix(PIN) for ln in got.stdout.splitlines() if ln.startswith(PIN)]


def discard(req: dict, q: Path | None = None) -> None:
    """REQ's kept verdict and its record removed once REQ is recorded: land.sh removes what it
    reads, but a batch item whose gate judged another tree, or a land refused before its pre-step,
    never read its own, and no later land is that enqueue's (GOH.BATCHPREFROM). Given its queue Q,
    every other verdict whose request is gone goes too (`reap`, GOH.LANDLEAKS), and the state root
    Q lives in is bounded: its logs, done records, ship results and run directories
    (lib/state_bounds.py sweep, GOH.LANDLOGPRUNE), once per recorded land."""
    if keep := (req.get("pre") or {}).get("keep"):
        for path in (Path(keep), Path(f"{keep}.record")):
            path.unlink(missing_ok=True)
        if req.get("repo"):
            _unpin(req["repo"], [Path(keep).name])
    if q is not None:
        reap(q, repo=req.get("repo"))
        state_bounds.sweep(q.parent)


def reap(q: Path, now_ns: int | None = None, repo: str | Path | None = None) -> list[Path]:
    """Every kept verdict under Q's `pre/` whose request is gone, removed with its record; the
    paths removed (GOH.LANDLEAKS: 14 files kept before `discard` existed outlived their lands).

    A verdict is a request's while a request in `pending/` or `running/` names it, and an
    enqueue's while it is younger than the enqueue's own bound (its name carries its birth in ns,
    lib/landq_inflight.py's pre-step bound plus margin): the pre-step keeps it BEFORE the request
    is filed. A record outlives no verdict of its own; given the REPO, no pin outlives its keep
    (GOH.PREONTIP)."""
    named: set[str] = set()
    for d in ("pending", "running"):
        for path in (q / d).glob("*.json") if (q / d).is_dir() else []:
            with contextlib.suppress(OSError, ValueError, AttributeError, TypeError):
                if keep := (json.loads(path.read_text()).get("pre") or {}).get("keep"):
                    named.add(Path(keep).name)
    bound = int(os.environ.get("GOH_ENQUEUE_PRE_TIMEOUT") or landq_inflight.PRE_TIMEOUT_S)
    oldest = (now_ns or time.time_ns()) - (bound + landq_inflight.MARGIN_S) * 10**9
    gone: list[Path] = []

    def held(name: str) -> bool:
        born = name.rpartition("-")[2]
        return name in named or (born.isdigit() and int(born) > oldest)

    for path in sorted((q / "pre").iterdir()) if (q / "pre").is_dir() else []:
        keep = path.with_name(path.name.removesuffix(".record"))
        if held(keep.name):
            continue
        with contextlib.suppress(OSError):
            path.unlink()
            gone.append(path)
    if repo:  # a pin goes with its keep; a running land's (land.sh read its keep) is held
        _unpin(repo, [n for n in _pins(repo) if not held(n) and not (q / "pre" / n).exists()])
    return gone


def timings_keep(log: Path | str) -> list[str]:
    """The `env` word that keeps a land's gate timings beside its log in the queue's `logs/`
    (GOH.LANDLEAKS): `<log stem>.timings.jsonl`, pruned with the log (lib/state_bounds.py), read
    by `goh land-profile` (lib/land_profile.py) and its step attribution. tools/land.sh moves its
    own there at exit."""
    return [f"GOH_LAND_TIMINGS_KEEP={str(log).removesuffix('.log')}.timings.jsonl"]


def lander_env(req: dict, keep: Path | str | None = None, also: list[dict] | None = None
               ) -> list[str]:  # fmt: skip
    """The lander's tools/land.sh reads the kept verdict from GOH_LAND_PRE_FROM: REQ's own keep, or
    KEEP (a batch gate's copy of it, `gate_copy`). ALSO, a batch's other requests: the tips their
    pre-steps judged, GOH_LAND_PRE_ALSO, which the gate's suite carries from too (GOH.PREONTIP)."""
    kept = [f"GOH_LAND_PRE_FROM={keep or req['pre']['keep']}"] if req.get("pre") else []
    shas = [j for r in also or () if SHA.fullmatch(j := str((r.get("pre") or {}).get("judged")))]
    kept += [f"GOH_LAND_PRE_ALSO={' '.join(shas)}"] if shas else []
    return [*kept, *land_slot.label(req)]


@contextlib.contextmanager
def gate_copy(q: Path, req: dict) -> Iterator[Path | None]:
    """A copy of REQ's kept verdict (and its record) for ONE gate run, removed after it
    (GOH.PRELANDBATCH): land.sh removes the verdict it reads, and a stack's gate that goes red is
    bisected into lone items, each of which then needs its own enqueue's verdict. None when REQ
    kept none, or its keep is gone."""
    keep = Path((req.get("pre") or {}).get("keep") or "")
    copy = q / "pre-gate" / f"{keep.name}.{os.getpid()}.{time.time_ns()}"
    made = [copy, Path(f"{copy}.record")]
    got: Path | None = None
    try:
        if req.get("pre") and keep.is_file():
            with contextlib.suppress(OSError):  # no copy is no verdict: the lander runs the set
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(keep, copy)
                if Path(f"{keep}.record").is_file():
                    shutil.copyfile(f"{keep}.record", made[1])
                got = copy
        yield got
    finally:
        for path in made:
            path.unlink(missing_ok=True)


def run_kept(req: dict, q: Path, script: Path, bindings: tuple[str, ...]) -> float:
    """`run` on REQ's worktree and sha, its green verdict kept under Q and named on REQ (`pre`);
    the lander's land.sh removes the keep once read (lib/land_pre_reuse.sh)."""
    keep = keep_path(q, req["sha"])
    noted: dict = {}
    did: dict = {}
    ran: dict = {}
    # In goh's slot the pre-step judges the tip rebased onto main (GOH.PREONTIP): never the lane's.
    onto = ["GOH_LAND_PRE_ONTO=1"] if req.get("slot") else []
    onto += main_red.env(req)  # main's idle-suite reds join the set (GOH.MAINREDNOTICE)
    took = run(Path(req["worktree"]), req["sha"], script, bindings, keep,
               [*land_slot.label(req), *onto], noted, did, ran)  # fmt: skip
    # The pre-step's wait, run, limit, workers and cpu_s: the SPD's figures (GOH.LANDPROFILEFIX),
    # always present (GOH.PREWIDTH) -- zeros, and why, when land.sh ran no pre-step at all.
    req["pre_run"] = ran or skipped("land.sh ran no pre-step (the repo declares no GOH_LAND_PRE)")
    if noted:
        req["pre_verdict"] = noted
    if did:  # took the lane's kept verdict, or ran the set and why (GOH.LANEPRESTEP)
        req["pre_affected"] = did
    adopt(req, keep)
    return took


def run(
    wt: Path,
    sha: str,
    script: Path,
    bindings: tuple[str, ...],
    keep: Path | None = None,
    label: list[str] | None = None,
    noted: dict | None = None,
    did: dict | None = None,
    took: dict | None = None,
) -> float:
    """Run SCRIPT's pre-step in WT; its seconds, or raise Red unless green or past its deadline. A
    green verdict is KEPT at `keep` (the judged sha; its record at `keep`.record) for the lander's
    tools/land.sh, which reuses it where the tip moved only in paths no test reads
    (GOH.PRELANDTWICE). A pre-step with no verdict fills NOTED (`no_verdict`); a green one's run
    of the set fills DID (lib/affected_verdict.py `outcome`: a kept verdict taken, or why not) and
    TOOK (`waited W s, ran R s of D s at K workers`, lib/land_spd.py `pre_run`)."""
    kept = [f"GOH_LAND_PRE_KEEP={keep}"] if keep else []
    kept += label or []
    fd, name = tempfile.mkstemp(prefix="goh-land-pretook.", dir=tmp_root.goh_tmp())
    os.close(fd)
    figures = Path(name)  # tools/land.sh land_pre_took writes it
    kept.append(f"GOH_LAND_PRE_TOOK={figures}")
    argv = ["env", *(f"-u{k}" for k in bindings), "GOH_LAND_PRE_ONLY=1", *kept, "bash", "-c",
            'cd -- "$1" && exec bash "$2"', "land", str(wt), str(script)]  # fmt: skip
    timeout = int(os.environ.get("GOH_ENQUEUE_PRE_TIMEOUT") or TIMEOUT)
    t0 = time.monotonic()
    info(f"pre-step before queueing {sha[:8]} (the lander's own, in {wt})")
    lines: list[str] = []
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            start_new_session=True)  # fmt: skip
    landq_inflight.note_child(proc.pid)  # what a re-enqueue or --cancel stops (GOH.REENQUEUECANCEL)
    timer = threading.Timer(timeout, lambda: killtree.stop_group(proc.pid, proc=proc))
    timer.start()
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            lines.append(line)
            sys.stderr.write(line)
        code = proc.wait()
    finally:
        timer.cancel()
        ran = took_figures(figures, lines)
    landq_inflight.bail()  # superseded meanwhile: no verdict, no request (GOH.REENQUEUECANCEL)
    if code == 0:
        if did is not None:
            did.update(affected_verdict.outcome(lines) or {})
        if took is not None:
            took.update(ran)
        if none := no_verdict(lines):
            if noted is not None:
                noted.update(none)
            warn(f"the pre-step had no verdict ({none['why']}); queued, the lander decides")
        return round(time.monotonic() - t0, 1)
    if clash := next((ln.strip() for ln in lines if "conflicts with main" in ln), ""):
        raise Red(clash)  # the slot's rebase onto main, before any test (GOH.PREONTIP)
    named = [ln.split()[1] for ln in lines if ln.startswith("FAILED ") and len(ln.split()) > 1]
    what = f"failing: {', '.join(named)}" if named else f"exit {code}, see the output above"
    detail = land_cause.excerpt("".join(lines))  # the record's log keeps it (GOH.PRESTEPLOG)
    raise Red(
        f"the pre-land tests failed on {sha[:8]} ({what}) -- fix them, or "
        "--no-pre REASON when this tip's set was run another way"
        + (f"\n{detail}" if detail else "")
    )
