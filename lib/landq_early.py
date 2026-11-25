"""The landing queue's early conflict check (GOH.EARLYCONFLICT): a pending land that no longer
stacks is red NOW, not when its turn comes.

WHY. A conflict was found only when the item reached the head of the queue and the batch stacked it
(lib/landq_batch.py, "does not stack on"). On 2026-11-18 GOH.CRATEFLOOR waited ~40 min behind
GOH.LANDTARGET's land, then went red in 0 s on a conflict with that land in gates/lcov_merge.py;
GOH.PRELANDTWICE did the same in tools/landq.py and lib/land_prestep.py. Both rebased by hand and
queued again: the 40 minutes bought nothing.

WHEN. After every pass of the lander (it holds the lander lock, so no request moves under it) --
which is the moment a land fast-forwarded main -- every pending land request is judged; and an
enqueue judges its own request FIRST, before its guards and pre-step (lib/land_check.py
`at_enqueue`, GOH.BATCHCONFLICTFIRST), and again before it files it (main may move during the
pre-step), a red one recorded and never queued.

WHAT. Each pending land is replayed onto main in objects only: each of the item's own commits
(merge-base(main, tip)..tip, less those whose patch main holds already, as a rebase drops them:
GOH.REENQUEUEREBASE, a re-enqueue whose partial landed was red after a 1411 s pre-step) is merged
onto the base by `git merge-tree --write-tree` (the cherry-pick a rebase makes, with land.sh's row
merge driver for `merge=goh-rows`) and a `commit-tree` carries it on. No worktree is checked out or
moved, no gate runs. A request that does not replay is red with the record the batch writes
(`conflict: did not stack on <main>`), its log naming the files and the commits on main since the
branch forked that touch them (their subjects name the landing). A git read that fails is no
verdict: the request stays pending for the batch.

AGAINST MAIN ONLY, not the pending items ahead (a deviation from the item's brief). Main never
moves back, so a conflict with it is a fact; a conflict with a request still pending is not -- that
request may be red, or bisected out, and then this one lands as it is. The batch still reds one
that does not stack on the item below it (lib/landq_batch.py), and once that item LANDS this check
reds the rest within the same lander pass: the brief's bound, "within a minute of that landing".

COST (measured 2026-11-18, the GOH.EARLYCONFLICT changelog): tens of ms per pending item, against
the minutes an item waits in the queue. Pinned by tests/test_landq_early.py.

The early conflict check (GOH.EARLYCONFLICT): after every lander pass (a land moved main) each
pending land is replayed onto main in objects only (`git merge-tree --write-tree` per own commit,
land.sh's row merge driver, `commit-tree`; no worktree moved, no gate) and one that conflicts is
red at once with the batch's `conflict: did not stack on` record, its log naming the files and the
commits on main that touch them; `--enqueue --land` does the same before filing, a red one recorded
and never queued. Judged against main only: a conflict with a request still pending is no verdict.
Invoked by `tools/landq.py`.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import gitrun
import landq_state

ROWS = Path(__file__).resolve().parent / "row_merge.py"
IDENT = {"GIT_AUTHOR_NAME": "goh", "GIT_AUTHOR_EMAIL": "goh@localhost",
         "GIT_COMMITTER_NAME": "goh", "GIT_COMMITTER_EMAIL": "goh@localhost"}  # fmt: skip
Record = Callable[[Path, dict, str], None]


class Unread(RuntimeError):
    """A git read the check needs failed: no verdict, the batch judges at the item's turn."""


def _git(repo: Path, *args: str, codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    try:
        done = gitrun.run_reader(args, repo=repo, timeout=60, env={**os.environ, **IDENT})
    except (subprocess.TimeoutExpired, OSError) as e:
        raise Unread(f"git {' '.join(args[:3])} in {repo}: {e}") from e
    if done.returncode not in codes:
        raise Unread(f"git {' '.join(args[:3])} in {repo}: exit {done.returncode}: "
                     f"{done.stderr.strip()}")  # fmt: skip
    return done


def rows() -> tuple[str, ...]:
    """land.sh's row merge driver, defined for this merge only (GOH.LANDUNION)."""
    if not ROWS.is_file():
        return ()
    driver = f"{shlex.quote(sys.executable)} {shlex.quote(str(ROWS))} %O %A %B %P"
    return ("-c", f"merge.goh-rows.driver={driver}")


def replay(repo: Path, base: str, upstream: str, tip: str) -> tuple[str, list[str]]:
    """TIP's own commits (UPSTREAM..TIP) replayed onto BASE as a rebase replays them, in objects
    only: (the replayed tip, []) or ('', the files the first conflicting commit conflicts in)."""
    if upstream == base:
        return tip, []
    # A commit whose patch is upstream already is dropped, as `git rebase` drops it: a re-enqueue
    # whose partial landed replays only what did not (GOH.REENQUEUEREBASE).
    listed = _git(repo, "rev-list", "--reverse", "--no-merges", "--right-only", "--cherry-pick",
                  f"{base}...{tip}", f"^{upstream}").stdout  # fmt: skip
    for commit in listed.split():
        done = _git(repo, *rows(), "merge-tree", "--write-tree", "--name-only", "--no-messages",
                    f"--merge-base={commit}^", base, commit, codes=(0, 1))  # fmt: skip
        lines = done.stdout.splitlines()
        if done.returncode == 1:
            return "", sorted({line for line in lines[1:] if line})
        base = _git(repo, "commit-tree", lines[0], "-p", base, "-m", "early").stdout.strip()
    return base, []


def judge(repo: Path, reqs: list[tuple[Path, dict]]) -> list[tuple[Path, dict, str]]:
    """The requests of REPO that do not replay onto main, each with its red outcome and log text."""
    main = _git(repo, "rev-parse", "HEAD").stdout.strip()
    reds = []
    for path, req in reqs:
        fork = _git(repo, "merge-base", main, req["sha"]).stdout.strip()
        if files := replay(repo, main, fork, req["sha"])[1]:
            reds.append((path, outcome(req, main), text(repo, req, (main, fork), files)))
    return reds


def outcome(req: dict, main: str) -> dict:
    """The record the batch writes for an item that does not stack (lib/landq_batch.py)."""
    return {"sha": req["sha"], "repo": req["repo"], "mode": "land", "state": "red",
            "why": f"conflict: did not stack on {main[:8]}", "early": True}  # fmt: skip


def text(repo: Path, req: dict, span: tuple[str, str], files: list[str]) -> str:
    """The red log: the files, and the commits on main since the fork that touch them."""
    main, fork = span
    touched = _git(repo, "log", "--format=%h %s", f"{fork}..{main}", "--", *files).stdout
    since = "".join(f"  {line}\n" for line in touched.splitlines()[:5]) or "  (none found)\n"
    branch = req.get("branch") or req["sha"][:8]
    return (f"✗ early conflict check (GOH.EARLYCONFLICT): {branch} conflicts with main {main[:8]}"
            f" in {' '.join(files)} -- found before its turn, nothing was rebased or gated; "
            f"{branch} is left at {req['sha'][:8]}; rebase it by hand -- nothing landed\n"
            f"landed on main since {branch} forked, touching those files:\n{since}")  # fmt: skip


def lands(q: Path, dirs: tuple[str, ...]) -> dict[str, list[tuple[Path, dict]]]:
    """The land requests under Q's DIRS, by repo, in queue order (a request's name is its time)."""
    out: dict[str, list[tuple[Path, dict]]] = {}
    rows = sorted((row for d in dirs for row in landq_state.rows(q, d)), key=lambda r: r[0].name)
    for path, req in rows:
        if req.get("mode") == "land" and req.get("sha"):
            out.setdefault(str(req.get("repo")), []).append((path, req))
    return out


def settle(q: Path, path: Path, red: dict, log: str, record: Record) -> None:
    """PATH's red outcome recorded as the lander records one: its log, done/, loopctl."""
    logged = q / "logs" / f"{path.stem}.log"
    logged.parent.mkdir(parents=True, exist_ok=True)
    with logged.open("a") as f:
        f.write(log)
    # the record and the request gone in one transaction (tools/landq.py record, GOH.LANDQDB)
    record(path, {**red, "log": str(logged)}, landq_state.request(path).get("item", ""))


def sweep(q: Path, record: Record, say: Callable[[str], None]) -> int:
    """Under the lander's lock: every pending land that no longer stacks, red now (module doc)."""
    n = 0
    for repo, reqs in lands(q, ("pending",)).items():
        try:
            reds = judge(Path(repo), reqs)
        except Unread as e:
            say(f"early conflict check of {repo}: {e} -- no verdict, left to the batch")
            continue
        for path, red, log in reds:
            try:  # a dead lander puts it back in pending
                claimed = landq_state.move(path, "running")
            except FileNotFoundError:
                continue  # gone meanwhile: a cancel, another lander's take
            say(f"{path.stem}: {red['why']} -- red now, before its turn (GOH.EARLYCONFLICT)")
            settle(q, claimed, red, log, record)
            n += 1
    return n


def at_enqueue(q: Path, req: dict, file: Callable[[dict, str], Path], record: Record) -> str:
    """REQ judged before it is filed: '' when it stacks (or no verdict); else its red log, the
    request filed by FILE under `early/` and recorded red, so the lander never sees it."""
    try:
        reds = judge(Path(req["repo"]), [(landq_state.name(q, "pending", f"~{req['sha']}"), req)])
    except Unread:
        return ""
    if not reds:
        return ""
    _, red, log = reds[0]
    settle(q, file(req, "early"), red, log, record)
    return log
