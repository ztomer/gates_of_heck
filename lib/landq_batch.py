"""The landing queue's batch (GOH.LANDBATCH): several pending lands of one repo, ONE land gate.

WHY. The lander gated one land at a time, each rebased on main and gated alone, so with three
lanes a lane waited about as long in the queue as it spent gated (2026-11-17: 572 agent-minutes
queued against 711 gated). Merge-queue style, the head of the queue now takes the land requests of
its repo behind it, in queue order, up to GOH_LANDQ_BATCH (default 3; 1 is the one-by-one queue).
A push or ship of that repo ends the take: it runs after what was enqueued before it.

THE STACK. Holding the repo's land lock (lib/land_lock.py, so no other land moves main meanwhile),
the lander rebases each item's branch onto the one below it -- the first onto main -- by the
lander's own tools/land.sh in its stacking mode (GOH_LAND_STACK_ONTO / _UPSTREAM: the same rebase,
row merge driver and conflict message a land has; only the item's own commits move, so an item
dropped from below is never carried). An item whose rebase conflicts is red (`conflict`, its log
the rebase's) and the rest stack without it. Then the TIP's land.sh runs as any land does -- its
pre-step, gate and fast-forward, under the lander's hold (GOH_LAND_LOCK_OWNER) -- and verdict carry
and reuse apply to the tip's tree as to any tree. The enqueue's kept pre-step verdict
(GOH_LAND_PRE_FROM) goes with a tip whose tree IS its enqueued commit's -- a lone item after a
bisect -- never with a stacked one, whose tree no enqueue judged (GOH.BATCHPREFROM).

THE VERDICT. Green lands every item: each done record names the item's OWN commit (an ancestor of
main, so `loopctl done` and a ship's ancestry check read it), and `goh land --wait ID` returns per
item. Red bisects: the first half is restacked on main and gated, then the second half on whatever
landed -- with the second half gated on main BESIDE the first, in the second gate slot, and used
when the first lands nothing (GOH.LANDBISECTPAR, lib/landq_spec.py); a lone red item is red with
ITS log (the run that judged its tree, under a header naming the batch). A stack whose tree an
earlier run already judged red is not gated again. A run past GOH_LAND_TIMEOUT is not bisected
(hours more of gates for what is most likely the machine): its whole stack is red, `timed out`.
Every record carries `batch`: {id, items, gates}, the gate runs the batch spent, which
`goh land --report` counts as gates per landed item (lib/landq_report.py).

A GIT READ THAT FAILS (GOH.BATCHGITFAIL). Every git read the batch makes is strict: a failure or a
timeout is a GitFailed, never an empty string standing for a tree, a commit or main's sha. The
batch step stops loudly (the error in the lander log and each stopped item's own log), the slots
it stacked (lib/land_slot.py: goh's checkouts, never a lane's tree) go back to their enqueued
commits, and what it had not yet judged comes back `pending`:
`hold` puts it back in pending/ for the lander's next pass, and after RETRIES passes records it red
with the git error. Items an earlier half already landed keep their records. A git failure AFTER the
gate landed the stack cannot take the land back: those items are recorded red, saying so (`landed,
but main's HEAD is unreadable`), with their enqueued sha, never an empty one.

Pinned by tests/test_landq_batch.py and tests/test_landq_batch_git.py.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import bounded_run
import gitrun
import land_lock
import land_prestep
import land_slot
import landq_report
import landq_spec

DEFAULT = 3
RETRIES = 3  # passes a git failure may keep a request pending before it is recorded red
PAUSE = 30.0  # seconds the lander waits before it tries again after one
READ_PAUSE = 1.0  # seconds between those reads
READ_TRIES = 3  # reads of main's HEAD after the gate landed the stack: it is too late to give up


class GitFailed(RuntimeError):
    """A git read the batch needs failed or timed out (GOH.BATCHGITFAIL)."""


def strict_git(repo: Path, *args: str, tries: int = 1) -> str:
    """`git ARGS` in REPO, its non-empty output; GitFailed after TRIES failed reads, saying why."""
    why = ""
    for attempt in range(tries):
        if attempt:
            time.sleep(READ_PAUSE)
        try:
            done = gitrun.run_reader(args, repo=repo, timeout=60)
        except (subprocess.TimeoutExpired, OSError) as e:
            why = f"{type(e).__name__}: {e}"
            continue
        if done.returncode == 0 and done.stdout.strip():
            return done.stdout.strip()
        why = f"exit {done.returncode}: {done.stderr.strip() or '(no output)'}"
    raise GitFailed(f"git {' '.join(args)} in {repo}: {why}")


def hold(q: Path, path: Path, outcome: dict, fails: dict[str, int], say: Callable[[str], None]
         ) -> dict | None:  # fmt: skip
    """The record for OUTCOME, or None when its request went back to pending/ (a git failure)."""
    if outcome.get("state") != "pending":
        fails.pop(path.stem, None)
        return outcome
    n = fails[path.stem] = fails.get(path.stem, 0) + 1
    if n >= RETRIES:
        fails.pop(path.stem)
        say(f"{path.stem}: git failed {n} passes in a row -- recorded red")
        return {**outcome, "state": "red", "why": f"git failed {n} passes: {outcome['why']}"}
    say(f"{path.stem}: git failed ({outcome['why']}) -- pending again, pass {n} of {RETRIES}")
    path.replace(q / "pending" / path.name)
    return None


def cap() -> int:
    """GOH_LANDQ_BATCH: the most land requests one gate judges; 1 is the one-by-one queue."""
    try:
        return max(1, int(os.environ.get("GOH_LANDQ_BATCH") or DEFAULT))
    except ValueError:
        return DEFAULT


def read(path: Path) -> dict:
    """A request, or {} when it is no JSON object (one broken file never stops the take)."""
    try:
        req = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return req if isinstance(req, dict) else {}


def pick(pending: list[Path]) -> list[Path]:
    """The requests the queue's head runs with: it alone, or (a land) it and the land requests of
    its repo behind it, in queue order, up to cap(); a push or ship of that repo ends the take."""
    head = read(pending[0])
    if head.get("mode") != "land" or cap() == 1:
        return pending[:1]
    out = [pending[0]]
    for path in pending[1:]:
        req = read(path)
        if len(out) == cap():
            break
        if req.get("repo") != head.get("repo"):
            continue
        if req.get("mode") != "land" or not req.get("worktree"):
            break
        out.append(path)
    return out


@dataclass
class Kit:
    """What the batch borrows from the lander (tools/landq.py)."""

    unset: list[str]  # `env -u...`: git's repository bindings, never inherited
    stacker: Path  # the lander's own land.sh: it knows the stacking mode whatever a lane's copy is
    land_script: Callable[[Path], Path]  # the land.sh that drives a lane's land: main's (D-0012)
    timeout: int
    grace: float
    say: Callable[[str], None]


@dataclass
class Item:
    path: Path
    req: dict
    log: Path
    tip: str = ""
    upstream: str = ""  # the commit below the item's own commits
    outcome: dict | None = None


@dataclass
class Batch:
    q: Path
    kit: Kit
    items: list[Item]
    gates: int = 0
    red_trees: dict[str, tuple[Path, str]] = field(default_factory=dict)
    spec: landq_spec.Spec | None = None  # the second half's run on main, in flight

    @property
    def id(self) -> str:
        return self.items[0].path.stem

    @property
    def main(self) -> Path:
        return Path(self.items[0].req["repo"])

    def git(self, repo: Path, *args: str) -> str:
        """A strict read (GitFailed); never '' standing for a tree or a sha."""
        return strict_git(repo, *args)

    def peek(self, repo: Path, *args: str) -> str:
        """A read whose failure is an answer ('' = unreadable): a worktree that may be gone."""
        try:
            return strict_git(repo, *args)
        except GitFailed:
            return ""

    def outcome(self, it: Item, state: str, log: Path, **extra: str) -> None:
        it.outcome = {"sha": extra.pop("sha", it.req["sha"]), "repo": it.req["repo"],
                      "mode": "land", "log": str(log), "state": state, **extra}  # fmt: skip

    def own_log(self, it: Item, run_log: Path, verdict: str) -> Path:
        """The item's log: a header naming the batch, then the run that judged its tree."""
        text = run_log.read_text(errors="replace") if run_log.is_file() else ""
        head = f"batch {self.id}: {verdict} by the gate run {run_log.name}\n"
        with it.log.open("a") as f:
            f.write(head + text)
        return it.log

    def stack(self, items: list[Item], trial: bool = False) -> list[Item] | None:
        """ITEMS rebased each onto the one below, the first onto main; a conflict drops one.
        A TRIAL (the speculative stack, lib/landq_spec.py) judges nothing: a conflict is None."""
        base, out = self.git(self.main, "rev-parse", "HEAD"), []
        for it in items:
            if it.upstream != base:
                argv = ["env", *self.kit.unset, *land_slot.label(it.req),
                        f"GOH_LAND_STACK_ONTO={base}", f"GOH_LAND_STACK_UPSTREAM={it.upstream}",
                        "bash", "-c",
                        'cd -- "$1" && exec bash "$2"', "stack", it.req["worktree"],
                        str(self.kit.stacker)]  # fmt: skip
                res = bounded_run.run_step(argv, 600, self.kit.grace, "land stack", str(it.log))
                if res.code or res.timed_out:
                    if trial:
                        return None
                    self.kit.say(f"batch {self.id}: {it.path.stem} does not stack on {base[:8]}")
                    self.outcome(it, "red", it.log, why=f"conflict: did not stack on {base[:8]}")
                    continue
                it.tip, it.upstream = self.git(Path(it.req["worktree"]), "rev-parse", "HEAD"), base
            out.append(it)
            base = it.tip
        return out

    def gate(self, top: Item, size: int) -> tuple[str, Path, str]:
        """The tip's land.sh, as a lane runs it by hand: (landed|red|timeout, its log, why)."""
        self.gates += 1
        log = self.q / "logs" / f"{self.id}.gate{self.gates}.log"
        wt = Path(top.req["worktree"])
        self.kit.say(f"batch {self.id}: gate {self.gates} on {top.tip[:8]}, {size} stacked")
        argv = ["env", *self.kit.unset, *self.pre_from(top), *land_prestep.timings_keep(log),
                "bash", "-c",
                'cd -- "$1" && exec bash "$2"', "land",
                str(wt), str(self.kit.land_script(wt))]  # fmt: skip
        res = bounded_run.run_step(argv, self.kit.timeout, self.kit.grace, "land batch", str(log))
        if res.timed_out:
            return "timeout", log, f"timed out after {self.kit.timeout}s"
        return ("landed" if res.code == 0 else "red"), log, f"exit {res.code}"

    def pre_from(self, top: Item) -> list[str]:
        """The `env` words naming TOP to land.sh: with the enqueue's kept verdict
        (GOH.BATCHPREFROM) only when the gated tree IS the enqueued commit's -- a stack moved it,
        and that tree is judged again."""
        req = top.req
        if not req.get("pre"):
            return land_slot.label(req)
        wt = Path(req["worktree"])
        gated, judged = (
            self.git(wt, "rev-parse", f"{sha}^{{tree}}") for sha in (top.tip, req["sha"])
        )
        return land_prestep.lander_env(req) if gated == judged else land_slot.label(req)

    def settle(self, items: list[Item]) -> None:
        """Gate ITEMS stacked; green lands them all, red splits them in two (module doc)."""
        stacked = self.stack(items)
        if not stacked:  # never None: not a trial
            return
        top = stacked[-1]
        tree = self.git(Path(top.req["worktree"]), "rev-parse", "HEAD^{tree}")
        if tree in self.red_trees:
            state, (log, why) = "red", self.red_trees[tree]
            self.kit.say(f"batch {self.id}: {top.tip[:8]}'s tree was gated red in {log.name}")
        else:
            state, log, why = self.gate(top, len(stacked))
        if state == "landed":
            self.landed(stacked, log)
            return
        if state == "timeout" or len(stacked) == 1:
            for it in stacked:
                self.outcome(it, "red", self.own_log(it, log, why), why=why)
            return
        self.red_trees[tree] = (log, why)
        half = len(stacked) // 2
        landq_spec.split(self, stacked[:half], stacked[half:])

    def on_main(self, tip: str, main: str) -> bool:
        """Whether TIP is an ancestor of MAIN: exit 1 is `no`, any other failure is GitFailed."""
        try:
            code = gitrun.run_reader(("merge-base", "--is-ancestor", tip, main), repo=self.main,
                                     timeout=60).returncode  # fmt: skip
        except (subprocess.TimeoutExpired, OSError) as e:
            raise GitFailed(f"git merge-base --is-ancestor in {self.main}: {e}") from e
        if code > 1:
            raise GitFailed(f"git merge-base --is-ancestor {tip[:8]} in {self.main}: exit {code}")
        return code == 0

    def landed(self, stacked: list[Item], log: Path) -> None:
        """The gate landed STACKED: each item's record names the commit main holds for it. The land
        cannot be taken back, so an unreadable main is red with the enqueued sha (never '')."""
        try:
            main = strict_git(self.main, "rev-parse", "HEAD", tries=READ_TRIES)
            shas = [it.tip if self.on_main(it.tip, main) else main for it in stacked]
        except GitFailed as e:
            why = f"landed, but main's HEAD is unreadable ({e}): check main before enqueueing again"
            self.kit.say(f"batch {self.id}: {why}")
            for it in stacked:
                self.outcome(it, "red", self.own_log(it, log, why), why=why)
            return
        for it, sha in zip(stacked, shas, strict=True):  # sha is main when land.sh rebased
            self.outcome(it, "landed", self.own_log(it, log, "landed"), sha=sha,
                         enqueued=it.req["sha"], landed=sha)  # fmt: skip

    def restore(self, it: Item) -> None:
        """A red item's branch back at its enqueued commit: stacked, it may sit on another item's
        commits that never landed, and the lane would land them with its own."""
        wt = Path(it.req["worktree"])
        if it.tip == it.req["sha"] or self.peek(wt, "rev-parse", "HEAD") != it.tip:
            return
        gitrun.run_writer(("reset", "-q", "--keep", it.req["sha"]), repo=wt, deadline_s=60)

    def stalled(self, live: list[Item], why: GitFailed) -> None:
        """A git read failed mid-step: what no half has judged yet is `pending` (module doc)."""
        self.kit.say(f"batch {self.id}: {why} -- left pending, tried again")
        for it in live:
            if it.outcome is None:
                self.outcome(it, "pending", it.log, why=str(why))
                with it.log.open("a") as f:
                    f.write(f"batch {self.id}: {why} -- nothing landed for this request; pending\n")

    def run(self) -> list[dict]:
        for it in self.items:
            if why := land_slot.ready(self.q, it.req):  # GOH.LANDSNAPSHOT: at its sha again
                it.log.write_text(f"{why} -- nothing landed; enqueue again\n")
                self.outcome(it, "red", it.log, why=why.split(":")[0])
                continue
            it.tip = self.peek(Path(it.req["worktree"]), "rev-parse", "HEAD")
            if it.tip != it.req["sha"]:
                at, want = it.tip or "(unreadable)", it.req["sha"]
                it.log.write_text(f"{it.req['worktree']} is at {at}, not the enqueued {want} "
                                  "-- nothing landed; enqueue again\n")  # fmt: skip
                self.outcome(it, "red", it.log, why="tip moved after enqueue")
        live = [it for it in self.items if it.outcome is None]
        self.kit.say(f"batch {self.id}: {len(live)} of {len(self.items)} lands of {self.main}")
        with land_lock.held(land_lock.path(self.main), f"landq batch {self.id}", self.kit.say):
            try:
                main = self.git(self.main, "rev-parse", "HEAD")
                for it in live:
                    it.upstream = self.git(self.main, "merge-base", main, it.tip)
                if live:
                    self.settle(live)
            except GitFailed as e:
                self.stalled(live, e)
            for it in live:
                if it.outcome and it.outcome["state"] != "landed":
                    self.restore(it)
        self.kit.say(f"batch {self.id}: {len(self.items)} items, {self.gates} gate runs")
        info = {"id": self.id, "items": len(self.items), "gates": self.gates}
        return [{**(it.outcome or {}), "batch": info} for it in self.items]


def run(q: Path, paths: list[Path], kit: Kit, run_one: Callable[[Path, Path], dict]) -> list[dict]:
    """The outcomes of the requests at PATHS (in running/, queue order), one per path: a lone
    request is RUN_ONE's, as before batches; the batch's items share its start and load."""
    if len(paths) == 1:
        return [landq_report.timed(run_one, q, paths[0])]
    started, load = dt.datetime.now(dt.UTC), landq_report.load1()
    items = [Item(p, json.loads(p.read_text()), q / "logs" / f"{p.stem}.log") for p in paths]
    return [landq_report.stamped(o, started, load) for o in Batch(q, kit, items).run()]
