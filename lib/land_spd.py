"""lib/land_spd.py -- what `goh land-profile` adds for the SPD exit (GOH.LANDPROFILEFIX).

OWNER, 2026-11-21: "let's see what we can do to actually achieve a 10m time (not 12m)". The same
day three hand counts of one day's lands gave 15.2, 31.0 and 41.6 min and `loopctl phase-goal SPD`
printed 45.3: the lands were counted by different starts and a batch's one gate run was counted on
every member. This module is the one definition, three parts:

    once(rows)         a BATCH'S SHARED RUNS COUNT ONCE: the members of a batch (the record's
                       `batch.id`) share one lander run -- its pre-land, its gate, its slot wait,
                       its steps -- so the owner (the member whose request names the batch, else
                       the first) keeps them and the others keep only what is their own (e2e,
                       enqueue pre-step, queue wait, retries, earlier attempts)
    first_attempt(..)  the START of a land's e2e: the earliest of the chain's requests, filings and
                       the item's enqueue logs (`<queue>/logs/enqueue-<ns>-<item>.log`, made when
                       `goh land --enqueue` starts) since the item's previous landing -- a red
                       enqueue pre-step is inside the e2e, and a refusal that filed no record
                       still has its log
    summary(..)        THE NUMBER the exit is judged by: commit to landed (the landed commit's
                       author date to the land record's `finished`), its median and the e2e's,
                       the share of lands whose lander ran the WHOLE pre-land set again (its log:
                       neither `not run again` nor the readers-only run), the share whose enqueue
                       pre-step ran the set instead of taking a kept verdict (`pre_affected`:
                       `ran` against `took`), and the enqueue pre-step's wait, run and worker count
                       (the record's `pre_run`, else the line `pre-land green in N s: waited W s,
                       ran R s of D s at K workers` of the enqueue's own log)

Pinned by tests/test_land_spd.py.
"""

from __future__ import annotations

import datetime as dt
import re
import statistics
import subprocess
from pathlib import Path

import land_e2e

OWN = ("e2e", "enqueue pre-step", "earlier attempts", "queue wait", "retries")
PRE_TOOK = re.compile(
    r"pre-land green in (\d+) s: waited (\d+) s, ran (\d+) s of (\d+|\?) s at (\d+|\?) workers"
)
ENQUEUED = re.compile(r"^✓ enqueued .*/pending/(\S+)\.json\s*$", re.MULTILINE)
REUSED = re.compile(r"pre-land: the verdict .* not run again")
NARROW = re.compile(r"pre-land: the verdict .* but what the moved paths' readers judge")
RERUN = re.compile(r"pre-land: the verdict .*run again|pre-land: no verdict from the enqueue")
RAN = re.compile(r"✓ pre-land green in \d+")


def pre_run(lines: list[str]) -> dict | None:
    """The pre-step's `waited W s, ran R s of D s at K workers` (tools/land.sh pre_took) from its
    output LINES, as the land record keeps it; None when no such line is there."""
    found = [m for ln in lines if (m := PRE_TOOK.search(ln))]
    if not found:
        return None
    wait, ran, limit, workers = (None if x == "?" else int(x) for x in found[-1].groups()[1:])
    return {"wait_s": wait, "run_s": ran, "limit_s": limit, "workers": workers}


def lander_kind(texts: list[str]) -> str | None:
    """What the lander did with the enqueue's pre-land verdict, from its logs: `reused` (not run
    again), `readers` (only the moved paths' readers), `whole` (the whole set again, a verdict that
    did not hold or none, lib/pre_reuse.py RERUN); None when the logs say none of it."""
    text = "\n".join(texts)
    if REUSED.search(text):
        return "reused"
    if NARROW.search(text):
        return "readers"
    return "whole" if RERUN.search(text) or RAN.search(text) else None


def once(rows: list[dict]) -> list[dict]:
    """ROWS with each batch's shared stages kept on its owner only (module doc)."""
    owner: dict[str, str] = {}
    for r in rows:
        b = r.get("batch")
        if b and (b not in owner or r["ref"] == b):
            owner[b] = r["ref"]
    out = []
    for row in rows:
        r = row
        if row.get("batch") and owner[row["batch"]] != row["ref"]:
            r = {**row, "stages": {k: v for k, v in row["stages"].items() if k in OWN},
                 "parents": {}}  # fmt: skip
        out.append(r)
    return out


def enqueue_logs(q: Path, item: str) -> list[tuple[dt.datetime, Path]]:
    """The enqueue logs of ITEM, each with the time its name says (`goh land --enqueue` start)."""
    slug = item.replace("/", "_")
    found = []
    for p in (q / "logs").glob(f"enqueue-*-{slug}.log"):
        head = p.name.removeprefix("enqueue-").partition("-")[0]
        if head.isdigit() and p.name == f"enqueue-{head}-{slug}.log":
            found.append((dt.datetime.fromtimestamp(int(head) / 1e9, dt.UTC), p))
    return sorted(found)


def first_attempt(q: Path, item: str, chain: list[tuple[Path, dict]], since: dt.datetime | None,
                  end: dt.datetime | None) -> dt.datetime | None:  # fmt: skip
    """The first enqueue attempt of the land CHAIN of ITEM (module doc): its records' `requested`
    and filing times, and the item's enqueue logs after SINCE (its previous landing), before END."""
    cands = [land_e2e.filed(p) for p, _ in chain]
    cands += [land_e2e._when(r["requested"]) for _, r in chain if r.get("requested")]
    cands += [t for t, _ in enqueue_logs(q, item)
              if (since is None or t > since) and (end is None or t < end)]  # fmt: skip
    cands = [t for t in cands if t is not None]
    return min(cands) if cands else None


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=False,
                          stdin=subprocess.DEVNULL)  # fmt: skip


def authored(repo: str, sha: str) -> dt.datetime | None:
    """The author date of commit SHA in REPO; None when git cannot say."""
    got = _git(repo, "show", "-s", "--format=%aI", sha)
    try:
        return dt.datetime.fromisoformat(got.stdout.strip()) if got.returncode == 0 else None
    except ValueError:
        return None


def resolves(repo: str, ref: str) -> str | None:
    """The commit REF names in REPO, else None."""
    got = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return got.stdout.strip() or None if got.returncode == 0 else None


def descends(repo: str, base: str, sha: str) -> bool:
    """Whether commit SHA is a strict descendant of BASE."""
    return base != sha and _git(repo, "merge-base", "--is-ancestor", base, sha).returncode == 0


def enqueue_pre(q: Path, row: dict) -> dict | None:
    """The row's enqueue pre-step run: its record's `pre_run`, else the line of the enqueue log that
    filed the row's request (`✓ enqueued ... pending/<stem>.json`)."""
    if row.get("pre_run"):
        return row["pre_run"]
    for _, log in reversed(enqueue_logs(q, row["item"])):
        try:
            text = log.read_text(errors="replace")
        except OSError:
            continue
        if any(s == row["ref"] for s in ENQUEUED.findall(text)):
            return pre_run(text.splitlines())
    return None


def _med(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def summary(q: Path, rows: list[dict]) -> dict:
    """The SPD figures over the landed ROWS (land_profile.land's): see the module doc."""
    c2l, e2e, whole, ran, took, wait, run, workers = [], [], 0, 0, 0, [], [], []
    judged = no_pre = 0
    for r in rows:
        done = authored(r["repo"], r["landed"]) if r.get("landed") and r.get("repo") else None
        if done is not None and r.get("end"):
            c2l.append((r["end"] - done).total_seconds())
        e2e.append(r["stages"]["e2e"][0])
        if r.get("kind"):
            judged += 1
            whole += r["kind"] == "whole"
        aff = r.get("pre_affected") or {}
        ran += "ran" in aff
        took += "took" in aff
        pre = enqueue_pre(q, r)
        if pre is None:
            no_pre += 1
            continue
        for key, into in (("wait_s", wait), ("run_s", run), ("workers", workers)):
            if pre.get(key) is not None:
                into.append(float(pre[key]))
    return {"lands": len(rows), "commit_to_landed_n": len(c2l), "commit_to_landed_s": _med(c2l),
            "commit_to_landed_max_s": max(c2l) if c2l else None, "e2e_s": _med(e2e),
            "whole_rerun": whole, "whole_rerun_of": judged,
            "whole_rerun_share": whole / judged if judged else None,
            "prestep_ran": ran, "prestep_took": took, "prestep_without_record": no_pre,
            "prestep_wait_s": _med(wait), "prestep_run_s": _med(run),
            "prestep_workers": _med(workers)}  # fmt: skip


def _min(s: float | None) -> str:
    return "-" if s is None else f"{s / 60:.1f} min"


def render(name: str, s: dict) -> str:
    """One summary block, three lines."""
    share = s["whole_rerun_share"]
    share = "-" if share is None else f"{share:.0%}"
    w, r, k = s["prestep_wait_s"], s["prestep_run_s"], s["prestep_workers"]
    pre = "-" if r is None else f"waited {w:.0f} s, ran {r:.0f} s at {k:.0f} workers (median)"
    return (
        f"== SPD {name}: {s['lands']} lands\n"
        f"commit to landed: median {_min(s['commit_to_landed_s'])}, max "
        f"{_min(s['commit_to_landed_max_s'])} (n={s['commit_to_landed_n']}); first attempt to "
        f"landed: median {_min(s['e2e_s'])}\n"
        f"lander re-ran the whole pre-land: {share} ({s['whole_rerun']}/{s['whole_rerun_of']}); "
        f"enqueue pre-step ran the set {s['prestep_ran']}, "
        f"took a kept verdict {s['prestep_took']}\n"
        f"enqueue pre-step: {pre}"
    )
