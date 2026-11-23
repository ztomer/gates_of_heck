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
gates_in_flight = importlib.import_module("gates_in_flight")
gitrun = importlib.import_module("gitrun")
land_cause = importlib.import_module("land_cause")
main_red = importlib.import_module("main_red")
pre_deadline = importlib.import_module("pre_deadline")
killtree = importlib.import_module("killtree")
land_slot = importlib.import_module("land_slot")
land_spd = importlib.import_module("land_spd")
landq_remote = importlib.import_module("landq_remote")
landq_inflight = importlib.import_module("landq_inflight")
state_bounds = importlib.import_module("state_bounds")
terse = importlib.import_module("terse")
tmp_root = importlib.import_module("tmp_root")
_tui = importlib.import_module("tui.lib")
info, warn = _tui.info, _tui.warn

# land.sh bounds the set at its share's deadline (lib/pre_deadline.py: at most GOH_LAND_PRE_CEIL,
# 25 min) after a wait for a short-lane slot (load.py's default bound, 15 min). This clock starts
# before both, so it covers both and a margin (the guards, the rebase): at 30 min it fired first on
# a busy box, and its kill read "the pre-land tests failed (exit -15)" -- GOH.NITS3 and
# GOH.ROADMAPIDS twice, each 1800 s after its pre-step started (GOH.PRESTEPDETACH).
TIMEOUT_MARGIN_S = 5 * 60
TIMEOUT = int(gates_in_flight.DEFAULT_WAIT_S + pre_deadline.CEILING_S) + TIMEOUT_MARGIN_S
# The words a pre-step this clock stopped is given: no verdict, like land.sh's own deadline.
BOUND_WHY = "stopped at the enqueue's own bound of {s} s"


class Red(Exception):
    pass


# tools/land.sh's one line for a pre-step that found nothing: a deadline ("no verdict after N s at
# load L") or a slot wait that expired ("no verdict: waited N s for a gate slot and ran nothing").
NO_VERDICT = re.compile(r"pre-land: no verdict[: ] ?(.*?) -- the gate decides")
# The diff coverage judge's own verdict (gates/diff_cov.py TAG): when it is what failed, the land's
# verdict line names it, not "the pre-land tests failed ... (exit 1)" (GOH.LANDCHECK2: GOH-TINY's
# two diff coverage reds said only that, 2026-11-22).
DIFFCOV = re.compile(
    r"(\[diff-coverage\] (?:\d+ of \d+ changed executable lines? uncovered"
    r"|\d+ allowlist entr(?:y|ies) stale))"
)


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
    out = (
        {k: v for k, v in got.items() if k in PRE_RUN_KEYS}
        if isinstance(got, dict)
        else land_spd.pre_run(lines) or {}
    )
    return landq_remote.took(figures, out)  # the worker's share (GOH.REMOTEMUX)


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
# GOH.CONFLICTKEEPVERDICT: a land red ONLY on a stack conflict keeps its green verdict here, one per
# item, for that item's next enqueue (`carried`): its pre-step asks lib/pre_reuse.py whether the
# verdict holds for the rebased tip, as the lander does, instead of running the whole set again
# (16-20 min a conflict at 2026-11-22's load). Read once; a day old, it is reaped.
CARRY, CARRY_PIN, CARRY_DAYS = "pre-carry", "carry-", 1


def carry_path(q: Path, item: str) -> Path:
    return q / CARRY / item.replace("/", "_")


def conflict_red(outcome: dict) -> bool:
    """OUTCOME is red on a stack conflict alone: the batch's or the early check's `conflict:`, or
    land.sh's rebase that conflicted (lib/land_cause.py's `conflict`, read from its log)."""
    if outcome.get("state") != "red":
        return False
    if str(outcome.get("why") or "").startswith("conflict"):
        return True
    try:
        log = Path(str(outcome.get("log") or "")).read_text(errors="replace")
    except OSError:
        return False
    return land_cause.classify(outcome, log) == "conflict"


def carried(q: Path, req: dict) -> Path | None:
    """REQ's item's carried verdict, when one names a commit REPO still has: its path, the judged
    sha put on REQ (`pre_carry`, which `adopt` accepts as the keep's verdict); else None."""
    path = carry_path(q, str(req.get("item") or "x/"))
    if not req.get("item") or not path.is_file():
        return None
    with contextlib.suppress(OSError, IndexError):
        judged = path.read_text().split()[0]
        held = gitrun.run_reader(("cat-file", "-e", f"{judged}^{{commit}}"), repo=req["repo"],
                                 timeout=60)  # fmt: skip
        if SHA.fullmatch(judged) and held.returncode == 0:
            req["pre_carry"] = judged
            info(f"pre-step: {req['item']}'s green verdict at {judged[:8]}, kept from its "
                 "conflict red, is asked first (GOH.CONFLICTKEEPVERDICT)")  # fmt: skip
            return path
    return None


def _carry(req: dict, keep: Path, q: Path) -> bool:
    """REQ's keep moved to its item's carry (module CARRY); its pin renamed with it. False when
    there is nothing to carry or the move failed (then it is discarded as any)."""
    if not (req.get("item") and keep.is_file()):
        return False
    dest = carry_path(q, req["item"])
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        Path(f"{dest}.record").unlink(missing_ok=True)
        if Path(f"{keep}.record").is_file():
            os.replace(f"{keep}.record", f"{dest}.record")
        os.replace(keep, dest)
    except OSError:
        return False
    if req.get("repo") and (judged := (req.get("pre") or {}).get("judged")):
        with contextlib.suppress(OSError, subprocess.SubprocessError):  # a pin is only a gc root
            gitrun.run_writer(("update-ref", f"{PIN}{CARRY_PIN}{dest.name}", judged),
                              repo=req["repo"], deadline_s=60)  # fmt: skip
    return True


def adopt(req: dict, keep: Path) -> None:
    """Put a kept green verdict on the request: `keep` names the very sha enqueued, or -- a request
    in a slot, whose pre-step rebased onto main first (GOH.PREONTIP) -- the rebased tip it judged
    and the main below it, `{"judged": P, "base": B}`; anything else is nothing."""
    with contextlib.suppress(OSError):
        words = keep.read_text().split()
        if words == [req["sha"]]:
            req["pre"] = {"judged": req["sha"], "keep": str(keep)}
        elif len(words) == 1 and words[0] == req.get("pre_carry"):  # a carried verdict held
            req["pre"] = {"judged": words[0], "keep": str(keep)}
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


def discard(req: dict, q: Path | None = None, carry: bool = False) -> None:
    """REQ's kept verdict and its record removed once REQ is recorded: land.sh removes what it
    reads, but a batch item whose gate judged another tree, or a land refused before its pre-step,
    never read its own, and no later land is that enqueue's (GOH.BATCHPREFROM). Given its queue Q,
    every other verdict whose request is gone goes too (`reap`, GOH.LANDLEAKS), and the state root
    Q lives in is bounded: its logs, done records, ship results and run directories
    (lib/state_bounds.py sweep, GOH.LANDLOGPRUNE), once per recorded land. CARRY (a red on a
    stack conflict alone): the green verdict goes to the item's carry instead (module CARRY)."""
    if keep := (req.get("pre") or {}).get("keep"):
        if carry and q is not None and _carry(req, Path(keep), q):
            info(f"{req['item']}: red on a conflict only -- its green pre-step verdict is kept "
                 f"for its next enqueue ({carry_path(q, req['item'])})")  # fmt: skip
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
    stale = (now_ns or time.time_ns()) / 10**9 - CARRY_DAYS * 86400
    for path in sorted((q / CARRY).iterdir()) if (q / CARRY).is_dir() else []:
        with contextlib.suppress(OSError):
            if path.stat().st_mtime < stale:
                path.unlink()
                gone.append(path)

    def kept(name: str) -> bool:
        if name.startswith(CARRY_PIN):
            return (q / CARRY / name.removeprefix(CARRY_PIN)).is_file()
        return held(name) or (q / "pre" / name).exists()

    if repo:  # a pin goes with its keep; a running land's (land.sh read its keep) is held
        _unpin(repo, [n for n in _pins(repo) if not kept(n)])
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
    if carry := carried(q, req):  # its conflict red's green verdict (GOH.CONFLICTKEEPVERDICT)
        onto.append(f"GOH_LAND_PRE_CARRY={carry}")
    slug = landq_inflight.slug(req.get("item") or Path(req["repo"]).name)
    log = q / "logs" / f"prestep-{time.time_ns():020d}-{slug}.log"  # GOH.TERSEOUT: its output
    took = run(Path(req["worktree"]), req["sha"], script, bindings, keep,
               [*land_slot.label(req), *onto], noted, did, ran, log)  # fmt: skip
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
    log: Path | None = None,
) -> float:
    """Run SCRIPT's pre-step in WT; its seconds, or raise Red unless green or past its deadline. A
    green verdict is KEPT at `keep` (the judged sha; its record at `keep`.record) for the lander's
    tools/land.sh, which reuses it where the tip moved only in paths no test reads
    (GOH.PRELANDTWICE). A pre-step with no verdict fills NOTED (`no_verdict`); a green one's run
    of the set fills DID (lib/affected_verdict.py `outcome`: a kept verdict taken, or why not) and
    TOOK (`waited W s, ran R s of D s at K workers`, lib/land_spd.py `pre_run`). Its output goes
    to LOG, which a red names, and reaches stderr only with --full (GOH.TERSEOUT, lib/terse.py):
    the foreground enqueue streamed every pytest line into the lane's context."""
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
    fired = threading.Event()  # this clock's kill is no verdict, never a red (GOH.PRESTEPDETACH)

    def bound() -> None:
        fired.set()
        killtree.stop_group(proc.pid, proc=proc)

    timer = threading.Timer(timeout, bound)
    timer.start()
    stream = terse.full() or log is None  # no log to name: the output is said, as it was
    sink = contextlib.ExitStack()
    try:
        out = None
        if log is not None:
            with contextlib.suppress(OSError):
                log.parent.mkdir(parents=True, exist_ok=True)
                out = sink.enter_context(log.open("w", encoding="utf-8", buffering=1))
        stream = stream or out is None
        for line in proc.stdout:  # type: ignore[union-attr]
            lines.append(line)
            if out is not None:
                out.write(line)
            if stream:
                sys.stderr.write(line)
        code = proc.wait()
    finally:
        sink.close()
        timer.cancel()
        ran = took_figures(figures, lines)
    landq_inflight.bail()  # superseded meanwhile: no verdict, no request (GOH.REENQUEUECANCEL)
    if code != 0 and fired.is_set():
        lines.append(f"pre-land: no verdict: {BOUND_WHY.format(s=timeout)} -- the gate decides\n")
        code = 0
    if code == 0:
        if not stream:  # green: its status lines (what it reused, ran, decided), no test output
            said = [ln for ln in lines if ln.startswith(terse.STATUS)][-terse.LIMIT :]
            sys.stderr.write("".join(said))
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
    above = "see the output above" if stream else f"its output is {log}"
    what = f"failing: {', '.join(named)}" if named else f"exit {code}, {above}"
    detail = land_cause.excerpt("".join(lines))  # the record's log keeps it (GOH.PRESTEPLOG)
    if not named and (cov := next((m[1] for ln in lines if (m := DIFFCOV.search(ln))), "")):
        uncovered = "\n".join(ln.rstrip() for ln in lines if ln.startswith("    ") and ":" in ln)
        raise Red(
            f"{cov} on {sha[:8]} (the pre-step's diff coverage judge) -- cover each line it "
            "names, or allowlist an unreachable one with its reason"
            + (f"\n{uncovered[:2000]}" if uncovered else "")
        )
    named_log = f"; log {log}" if named and not stream else ""
    if not detail and not stream:  # no pytest section: what the run marked, and its last lines
        detail = "\n".join(terse.summary("".join(lines), terse.LIMIT - 2))
    raise Red(
        f"the pre-land tests failed on {sha[:8]} ({what}) -- fix them, or "
        f"--no-pre REASON when this tip's set was run another way{named_log}"
        + (f"\n{detail}" if detail else "")
    )
