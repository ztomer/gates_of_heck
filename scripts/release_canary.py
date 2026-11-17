#!/usr/bin/env python3
"""release_canary.py -- a goh change that turns a consumer red is refused before it lands.

v0.57.4 (GOH.SHSEV, 2026-11-02) made `goh shell-lint` judge warnings and seeded only the repos that
round touched. Every consumer runs the gates checkout's HEAD at its next push, so koffee_big,
scripts, four games and two servers refused every push for five days, and nothing in goh could
have said so: its own gate judged goh, never the estate it ships to.

This is the replay. The native gates of the LAST RELEASE (the newest `v*` tag before HEAD) and of
HEAD each run over every registered consumer -- `goh structural --full --keep-going` in a clean
clone of the consumer's committed HEAD, which is what its push judges -- and the two verdicts are
compared step by step. Which gates a release changes is read from what they DO, not from a list:
a step green under the last release and red under HEAD is this release turning that repo red, and
the release is REFUSED, naming each repo, the step and the seed or fix it needs. A step red on both
sides is the consumer's own debt: named, never this release's refusal. Fail closed: a consumer the
canary could not judge, or an estate with no consumer in it, is never a clear release.

The estate is `lib/estate_registry.py`'s (the registry `install.sh` writes, unioned with the walk
of `GOH_ESTATE_ROOT`), the gated repos in it, and never gates_of_heck itself -- it is the subject.
When nothing a gate runs changed since the last release (only docs, tests or prose), nothing is
swept. The result is recorded under `release_canary` in the estate state file, which
`hooks/claude/estate_advisories.py` reads at every session start.

THE GRACE'S END (GOH.CANARYGRACE). `crate::grace` holds a new scanner shape back until its date
(`OVER_FROM`), and a push judges under the day, so the passes above could clear a release whose red
appears only when the grace ends. While the grace runs, HEAD's pass runs once more per consumer at
GOH_GRACE=over -- the same pass, one more side -- and every step red there and not under the day is
NAMED under `grace_over` in the record (count, repos, steps), never this release's refusal before
the date; `goh ship` carries it into its result and card (lib/ship_canary.py). From the date the
day's pass judges it, so the extra pass stops (`--grace auto`; `on`/`off` force it).

It is a step of this repo's own gate (`.gatesrc` GOH_CI_STEPS), so `tools/land.sh` runs it before
main moves -- the moment consumers start running the change -- and the push runs it again.

    python3 scripts/release_canary.py                    # the last release against HEAD
    python3 scripts/release_canary.py --base v0.57.3     # any earlier revision as the base

`--base-bin`/`--head-bin` name a side's binary outright (a fixture, in tests); its GOH_DIR is then
the `--goh` tree.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

# Each directory on the path right before what it holds: a path insert is not code to ruff (E402).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "checks"))
from _gitutil import foreign_repo_env

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tui.lib import counted, err, info, ok, warn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import canary_grace
import estate_registry
import killtree
import not_applicable
import pgid_cpu
from canary_sides import export, git_run as _git, resolve

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gates"))
import step_tools  # crate::grace's date, read from grace.rs (its one home)

ROOT = Path(__file__).resolve().parent.parent
KEY = "release_canary"
RUN_TIMEOUT_S = 900  # the STALL bound; the wall backstop is WALL_FACTOR times it
WALL_FACTOR = 4  # stepcmd::gate_bounds, as checks/_empty_scope_run.py
JOBS = 4
# What no gate runs: a change confined to these sweeps nothing.
NOT_GATE_INPUTS = (":(exclude)docs", ":(exclude)tests", ":(exclude)*.md")
# GOH_* keys a consumer's push does not carry are gate bookkeeping of the run that called this
# (local_ci.sh exports goh's own .gatesrc); only cache placement, the live switch and the private
# seed store pass through. GOH_SEED_DIR is a LOCATION, not an opt-in: dropped, every scanner read
# the live store, so a lane could not stage a seed for its land without writing the machine's own
# (GOH.CANARYSEEDDIR; divoom v0.45.12).
KEEP_ENV = {
    "GOH_BIN_STORE",
    "GOH_HEAD_CACHE",
    "GOH_VERDICT_DIR",
    "GOH_VERDICT_CACHE",
    "GOH_LIVE",
    "GOH_SEED_DIR",
}
# The ratchets a repo seeds: the step's name as `goh structural` prints it, and the command.
SEEDS = {
    "shell lint": "goh shell-lint --seed",
    "no early-exit pipe under pipefail": "goh early-exit-pipe --seed",
    "every child process names its stdin": "goh subprocess-stdin --seed",
}
STEP_HEAD = re.compile(r"^· (.+) \(≤\d+s\)$")
STEP_OK = re.compile(r"^✓ (.+)$")
STEP_RED = re.compile(r"^✗ structural: (.+) failed \(")
FOUND = re.compile(r"^✗ \[")
# The house's words for a bound that fired (bounded_run.py, stepcmd.rs, _empty_scope_run.py).
TIMED_OUT = "TIMED OUT after"


@dataclass
class Side:
    label: str
    bin: Path
    gohdir: Path
    live: Path  # the gates checkout an export's binary is resolved from (GOH_LIVE_ROOT)
    grace: str | None = None  # GOH_GRACE for this side; None: the day decides, as at a push


@dataclass
class Verdict:
    green: set[str] = field(default_factory=set)
    red: set[str] = field(default_factory=set)
    found: dict[str, list[str]] = field(default_factory=dict)
    # {step: its TIMED OUT line}: a step red ONLY because a bound fired gave no verdict.
    timed_out: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def last_release(goh: Path) -> str | None:
    """The newest release tag strictly before HEAD: HEAD may itself carry the tag being cut."""
    r = _git(goh, "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "HEAD^")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def gate_inputs_changed(goh: Path, base: str) -> list[str] | None:
    """The files a gate runs that differ between `base` and HEAD (None: git could not say)."""
    r = _git(goh, "diff", "--name-only", base, "HEAD", "--", ".", *NOT_GATE_INPUTS)
    return r.stdout.split() if r.returncode == 0 else None


def consumers(root: Path, state: Path, goh: Path) -> tuple[list[Path], list[str]]:
    """(every gated repo of the estate but gates_of_heck itself, the registry's problems)."""
    estate, problems = estate_registry.union(
        estate_registry.walk_repos(root), estate_registry.registry_path(state)
    )
    own = _common_dir(goh)
    return [r for r in estate if (r / ".gatesrc").is_file() and _common_dir(r) != own], problems


def _common_dir(repo: Path) -> str:
    r = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return str(Path(r.stdout.strip()).resolve()) if r.returncode == 0 else f"?{repo}"


def _env(side: Side) -> dict:
    env = {k: v for k, v in foreign_repo_env().items() if not k.startswith("GOH_") or k in KEEP_ENV}
    rest = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p and p != str(side.gohdir)]
    env.update(
        GOH_DIR=str(side.gohdir),
        GOH_BIN=str(side.bin),
        GOH_LIVE_ROOT=str(side.live),
        PYTHONPATH=os.pathsep.join([str(side.gohdir), *rest]),
        GIT_LFS_SKIP_SMUDGE="1",
    )
    if side.grace:
        env["GOH_GRACE"] = side.grace
    return env


def _starved(block: list[str]) -> str | None:
    """A step's TIMED OUT line, when its output carries no `✗` finding of its own; else None."""
    lines = [ln.strip() for ln in block]
    if any(ln.startswith("✗") for ln in lines):
        return None
    return next((ln[:200] for ln in lines if TIMED_OUT in ln), None)


def parse(rc: int, out: str) -> Verdict:
    """One `goh structural --full --keep-going` run, step by step. An exit that is not 0 or 1, or
    one its output does not account for, is an ERROR -- never read as green.

    A red step whose output says TIMED OUT and carries no `✗` finding of its own was STARVED
    (GOH.CANARYLOAD: Adv360's empty-scope sweep at load ~80 read as "turned red -- fix the
    findings"): it lands in `timed_out`, never in `red`. A timeout beside a finding stays red."""
    v, step, block = Verdict(), None, []
    for line in out.splitlines():
        if m := STEP_HEAD.match(line):
            step, block = m.group(1), []
        elif m := STEP_RED.match(line):
            if why := _starved(block):
                v.timed_out[m.group(1)] = why
            else:
                v.red.add(m.group(1))
        elif (m := STEP_OK.match(line)) and m.group(1) != "all structural gates passed":
            v.green.add(m.group(1))
        elif FOUND.match(line) and step:
            v.found.setdefault(step, []).append(line[1:].strip())
        block.append(line)
    tail = " | ".join(out.strip().splitlines()[-3:])
    if rc not in (0, 1):
        v.error = f"exit {rc}: {tail}"
    elif rc == 1 and not v.red and not v.timed_out:
        v.error = f"exit 1 naming no failed step: {tail}"
    elif rc == 0 and (v.red or not v.green):
        v.error = f"exit 0 over {len(v.red)} red, {counted(len(v.green), 'green step')}: {tail}"
    return v


def structural(side: Side, tree: Path) -> Verdict:
    """One side's native gates over one consumer, in a session of its own: a run that STALLS for
    RUN_TIMEOUT_S (no CPU, nothing runnable) or outlasts four times it is killed with everything
    it spawned (shellcheck, the probes), never left running. A wall ceiling alone read a run
    starved at load 80-150 as the consumer's refusal (GOH.CANARYFLAKE; the sweep's own bound,
    GOH.CANARYLOAD): the machine was slow, not the tree. Output goes to an unlinked file, so
    nothing waits on a pipe while the run is judged by what it spends."""
    with tempfile.TemporaryFile() as sink:
        try:
            proc = subprocess.Popen(
                [str(side.bin), "structural", "--full", "--keep-going"],
                cwd=tree,
                env=_env(side),
                stdout=sink,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
            )
        except OSError as e:
            return Verdict(error=str(e))
        expired = pgid_cpu.wait_for_exit(
            proc, killtree.session_pgid(proc), RUN_TIMEOUT_S * WALL_FACTOR, "goh structural",
            cpu_timeout=None, stall=RUN_TIMEOUT_S,
        )  # fmt: skip
        if expired is not None:
            killtree.stop_group(proc.pid, proc=proc)  # its own session: TERM, then KILL
            proc.wait()
            return Verdict(error=f"{expired}, killed")
        sink.seek(0)
        return parse(proc.returncode, sink.read().decode("utf-8", "replace"))


def remedy(step: str, found: list[str]) -> str:
    seed = SEEDS.get(step)
    if seed and not any(": error:" in f for f in found):
        return f"run `{seed}` in the repo and commit the seed (or fix the findings)"
    return "fix the findings (`goh structural --full --keep-going` in the repo lists them)"


def _fresh(tree: Path) -> None:
    """The clone as committed again: a side's run leaves nothing the next side reads."""
    _git(tree, "reset", "-q", "--hard")
    _git(tree, "clean", "-q", "-fdx")


def grace_end(over: Side, tree: Path, now: Verdict) -> dict:
    """{turned_red, errors, seconds}: HEAD once more at the grace's end, against its pass under the
    day (`now`). A step red only there is NAMED; nothing here refuses the release."""
    _fresh(tree)
    t0 = time.monotonic()
    then = structural(over, tree)
    secs = round(time.monotonic() - t0, 1)
    if then.error:
        return {"turned_red": [], "errors": [f"under {over.label}: {then.error}"], "seconds": secs}
    flips = [{"step": s, "found": then.found.get(s, [])[:3]} for s in sorted(then.red - now.red)]
    starved = [
        f"under {over.label}: {s} gave no verdict ({why})"
        for s, why in sorted(then.timed_out.items())
        if s not in now.red
    ]
    return {"turned_red": flips, "errors": starved, "seconds": secs}


def judge(repo: Path, base: Side, head: Side, scratch: Path, over: Side | None = None) -> dict:
    """{turned_red: [...], already_red: [...], errors: [...]} for one consumer; with `over`, HEAD
    at the grace's end too, under `grace` (grace_end)."""
    tree = scratch / "tree"
    env = {**foreign_repo_env(), "GIT_LFS_SKIP_SMUDGE": "1"}
    c = subprocess.run(
        ["git", "clone", "-q", "--shared", "--no-tags", str(repo), str(tree)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if c.returncode != 0:
        return {"errors": [f"cloning it: {c.stderr.strip()}"]}
    was = structural(base, tree)
    _fresh(tree)
    t0 = time.monotonic()
    now = structural(head, tree)
    head_secs = round(time.monotonic() - t0, 1)
    if now.error:
        return {"errors": [f"under {head.label}: {now.error}"]}
    # A base that could not judge (a .gatesrc key it predates, say) proves no step green: every red
    # under HEAD then counts as this release's, the direction that refuses.
    before = None if was.error else was.red
    out: dict = {"turned_red": [], "already_red": [], "errors": []}
    # Starved, not red: no verdict, so the release is not clear (fail closed), and it says why. A
    # step that timed out under the BASE is not in `was.red`, so it cannot excuse a red under HEAD.
    for step, why in sorted(now.timed_out.items()):
        out["errors"].append(
            f"under {head.label}: {step} gave no verdict ({why}) -- a starved run, not a finding;"
            " land again when the machine is quieter"
        )
    for step in sorted(now.red):
        if before is not None and step in before:
            out["already_red"].append(step)
        else:
            found = now.found.get(step, [])
            out["turned_red"].append(
                {"step": step, "remedy": remedy(step, found), "found": found[:3]}
            )
    if was.error and out["turned_red"]:
        out["base_error"] = was.error
    out["head_seconds"] = head_secs
    if over is not None:
        out["grace"] = grace_end(over, tree, now)
    return out


def _where(repo: Path, root: Path) -> str:
    try:
        return str(repo.relative_to(root))
    except ValueError:
        return str(repo)


def sweep(
    repos: list[Path], root: Path, base: Side, head: Side, jobs: int, over: Side | None = None
) -> dict:
    turned, already, errors, grace, t0 = {}, {}, [], canary_grace.empty(), time.monotonic()
    with (
        tempfile.TemporaryDirectory(prefix="goh-release-canary-") as tmp,
        cf.ThreadPoolExecutor(max_workers=jobs) as pool,
    ):
        futs = {
            pool.submit(judge, r, base, head, Path(tmp) / str(i), over): r
            for i, r in enumerate(repos)
        }
        for fut in cf.as_completed(futs):
            where, got = _where(futs[fut], root), fut.result()
            if got.get("turned_red"):
                turned[where] = got["turned_red"]
            if got.get("already_red"):
                already[where] = got["already_red"]
            errors += [f"{where}: {e}" for e in got.get("errors", [])]
            canary_grace.fold(grace, where, got)
    out = {"turned_red": turned, "already_red": already, "errors": sorted(errors)}
    out["sweep_seconds"] = round(time.monotonic() - t0, 1)
    if over is not None:
        out["grace_over"] = canary_grace.closed(grace)
    return out


def _sides(args: argparse.Namespace, tmp: Path) -> tuple[Side | None, Side | None, list[str]]:
    goh = args.goh
    if args.base_bin and args.head_bin:
        return (
            Side(args.base_label or "base", args.base_bin, goh, goh),
            Side(args.head_label or "HEAD", args.head_bin, goh, goh),
            [],
        )
    sides, errors = [], []
    for rev, label in ((args.base, args.base_label or args.base), ("HEAD", args.head_label)):
        label = label or _git(goh, "describe", "--tags", "--always", "HEAD").stdout.strip()
        native, why = resolve(goh, rev)
        problem = why or export(goh, rev, tmp / label.replace("/", "_"))
        if problem:
            errors.append(f"{label}: {problem}")
            sides.append(None)
        else:
            sides.append(Side(label, native, tmp / label.replace("/", "_"), goh))
    return sides[0], sides[1], errors


def run(args: argparse.Namespace) -> tuple[dict, int]:
    now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    rec: dict = {"ran_at": now, "turned_red": {}, "already_red": {}, "errors": [], "problems": []}
    rec["head_sha"] = _git(args.goh, "rev-parse", "HEAD").stdout.strip()  # what a ship matches
    rec["grace_over"] = {"ran": False, "count": None, "why": "nothing was swept"}
    explicit = bool(args.base_bin and args.head_bin)
    if not explicit:
        args.base = args.base or last_release(args.goh)
        if not args.base:
            rec.update(verdict="refused", errors=["no release tag before HEAD to compare with"])
            return rec, 1
        changed = gate_inputs_changed(args.goh, args.base)
        if changed is None:
            rec.update(verdict="refused", errors=[f"git cannot diff {args.base}..HEAD"])
            return rec, 1
        rec.update(base=args.base, changed=len(changed))
        if not changed:
            rec.update(verdict="no-gate-change", consumers=0)
            return rec, 0
    repos, problems = consumers(args.root, args.state, args.goh)
    rec.update(consumers=len(repos), problems=problems)
    registry = estate_registry.registry_path(args.state)
    # No estate at all -- no registry, no consumer under the root: a clean machine. A hosted runner
    # says the canary is NOT APPLICABLE; anywhere else it refuses below, as it always did, because
    # the owner's machine is the judge of record (lib/not_applicable.py).
    if (
        not repos
        and not registry.exists()
        and not_applicable.declare(
            "the release canary", f"the consumer estate (registry {registry}, root {args.root})"
        )
    ):
        rec.update(verdict="not-applicable")
        return rec, not_applicable.EXIT
    if not repos:
        why = f"no consumer in the estate (registry {registry},"
        rec.update(verdict="refused", errors=[f"{why} root {args.root}): a sweep of nothing"])
        return rec, 1
    with tempfile.TemporaryDirectory(prefix="goh-release-canary-sides-") as tmp:
        base, head, errors = _sides(args, Path(tmp))
        if errors:
            rec.update(verdict="refused", errors=errors)
            return rec, 1
        rec.update(base=base.label, head=head.label)
        when = step_tools.over_from(str(head.gohdir)) or step_tools.over_from(str(args.goh))
        want, why = canary_grace.grace_pass(args.grace, when, time.time())
        over = (
            replace(head, label=f"{head.label} at GOH_GRACE=over", grace="over") if want else None
        )
        rec["grace_over"] = {"ran": False, "count": None, "why": why}
        rec.update(sweep(repos, args.root, base, head, args.jobs, over))
        rec["grace_over"]["over_from"] = canary_grace.day(when)
    bad = rec["turned_red"] or rec["errors"]
    rec["verdict"] = "refused" if bad else "clear"
    return rec, 1 if bad else 0


def say(rec: dict) -> None:
    base, head = rec.get("base", "the last release"), rec.get("head", "HEAD")
    if rec["verdict"] == "not-applicable":
        return  # said where it was found (not_applicable.declare)
    if rec["verdict"] == "no-gate-change":
        ok(f"release canary: no gate input changed since {base} -- nothing to sweep")
        return
    for where, steps in sorted(rec["already_red"].items()):
        warn(f"release canary: {where} is red at push under {base} AND {head} (its own debt, not")
        warn(f"  this release's): {', '.join(steps)}")
    for p in rec["problems"]:
        warn(f"estate registry: {p}")
    for e in rec["errors"]:
        err(f"release canary could not judge {e}")
    if rec["turned_red"]:
        n = counted(len(rec["turned_red"]), "consumer")
        err(f"release canary: {head} turns {n} red at push that {base} left green:")
        for where, flips in sorted(rec["turned_red"].items()):
            for f in flips:
                err(f"  {where}: {f['step']} -- {f['remedy']}")
                for line in f["found"]:
                    info(f"      found: {line}")
    canary_grace.say(rec.get("grace_over") or {})
    if rec["verdict"] == "refused":
        err("release canary: REFUSED -- fix or seed each consumer above (in that repo), then land")
        err("  again; a consumer that should leave the estate leaves its registry, by name")
    else:
        ok(f"release canary: {head} turned no consumer red over {base} ({rec['consumers']} judged)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("GOH_ESTATE_ROOT", Path.home() / "Projects")),
    )
    ap.add_argument("--state", type=Path, default=estate_registry.state_file())
    ap.add_argument("--goh", type=Path, default=ROOT, help="the gates_of_heck tree being released")
    ap.add_argument("--base", help="the revision consumers run now (default: the last release tag)")
    ap.add_argument("--base-bin", type=Path, help="the base side's binary (a fixture, in tests)")
    ap.add_argument("--head-bin", type=Path, help="the head side's binary (a fixture, in tests)")
    ap.add_argument("--base-label")
    ap.add_argument("--head-label")
    ap.add_argument("--jobs", type=int, default=JOBS)
    ap.add_argument(
        "--grace",
        choices=("auto", "on", "off"),
        default="auto",
        help="HEAD once more at GOH_GRACE=over (auto: while crate::grace's date is ahead)",
    )
    args = ap.parse_args()
    args.root, args.goh = args.root.resolve(), args.goh.resolve()
    rec, rc = run(args)
    estate_registry.update_state(args.state, lambda old: {**old, KEY: rec})
    say(rec)
    return rc


if __name__ == "__main__":
    sys.exit(main())
