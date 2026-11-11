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
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "checks"))
from _gitutil import foreign_repo_env

sys.path.insert(0, str(ROOT))
from tui.lib import err, info, ok, warn

sys.path.insert(0, str(ROOT / "lib"))
import estate_registry
import killtree
import not_applicable

KEY = "release_canary"
RUN_TIMEOUT_S = 900
BUILD_TIMEOUT_S = 1800
JOBS = 4
# What no gate runs: a change confined to these sweeps nothing.
NOT_GATE_INPUTS = (":(exclude)docs", ":(exclude)tests", ":(exclude)*.md")
# GOH_* keys a consumer's push does not carry are gate bookkeeping of the run that called this
# (local_ci.sh exports goh's own .gatesrc); only cache placement and the live switch pass through.
KEEP_ENV = {"GOH_BIN_STORE", "GOH_HEAD_CACHE", "GOH_VERDICT_DIR", "GOH_VERDICT_CACHE", "GOH_LIVE"}
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


@dataclass
class Side:
    label: str
    bin: Path
    gohdir: Path
    live: Path  # the gates checkout an export's binary is resolved from (GOH_LIVE_ROOT)


@dataclass
class Verdict:
    green: set[str] = field(default_factory=set)
    red: set[str] = field(default_factory=set)
    found: dict[str, list[str]] = field(default_factory=dict)
    error: str | None = None


def _git(where: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(where), *args],
        env=foreign_repo_env(),
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def last_release(goh: Path) -> str | None:
    """The newest release tag strictly before HEAD: HEAD may itself carry the tag being cut."""
    r = _git(goh, "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "HEAD^")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def gate_inputs_changed(goh: Path, base: str) -> list[str] | None:
    """The files a gate runs that differ between `base` and HEAD (None: git could not say)."""
    r = _git(goh, "diff", "--name-only", base, "HEAD", "--", ".", *NOT_GATE_INPUTS)
    return r.stdout.split() if r.returncode == 0 else None


def resolve(goh: Path, rev: str) -> tuple[Path | None, str]:
    """(the native goh built from `rev`, why not): the gate's own resolver, which takes the build
    from the shared store or builds that commit into it (gates/_goh_bin.sh, build-goh.sh --rev)."""
    sha = _git(goh, "rev-parse", "--verify", "-q", f"{rev}^{{commit}}").stdout.strip()
    if not sha:
        return None, f"{rev} is not a commit of {goh}"
    script = (
        '. "$1/gates/_goh_bin.sh" && _goh_resolve_uncached "$1/gates" "$2"'
        ' && printf "%s\\n%s" "$goh_native" "$goh_native_why"'
    )
    env = {k: v for k, v in foreign_repo_env().items() if not k.startswith("GOH_")}
    try:
        r = subprocess.run(
            ["bash", "-c", script, "_", str(goh), sha],
            env=env,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"resolving goh at {rev}: {e}"
    native, _, why = r.stdout.partition("\n")
    if native and os.access(native, os.X_OK):
        return Path(native), ""
    tail = (r.stderr.strip().splitlines() or [""])[-1]
    return None, f"no goh binary for {rev}: {why or tail or f'exit {r.returncode}'}"


def export(goh: Path, rev: str, dest: Path) -> str | None:
    """`git archive rev` into `dest` -- the gates the binary delegates to, as of that revision --
    shaped as a consumer's gate gets them (gates/_from_head.sh): `.goh-head` names the commit, so
    a gate script run from it runs in place and resolves that commit's binary. Without it a script
    there walked up for a checkout, found none, and judged differently (sys_updater's empty-scope
    probe read red here and green at its push)."""
    sha = _git(goh, "rev-parse", "--verify", "-q", f"{rev}^{{commit}}").stdout.strip()
    if not sha:
        return f"{rev} is not a commit of {goh}"
    dest.mkdir(parents=True)
    # NOT `git archive | tar -x` (GOH.SUITEFLAKES). bsdtar exits at the archive's end marker
    # without draining the pipe, so git's last write of record padding, landing after that, takes
    # SIGPIPE: exit 141 with NOTHING on stderr, and `pipefail` made that the release canary's
    # "exporting v0.96.0: " with an empty reason (once, 2026-11-11). The archive is read whole,
    # then handed to tar, which may stop reading wherever it likes.
    archive = subprocess.run(
        ["git", "-C", str(goh), "archive", sha],
        env=foreign_repo_env(),
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if archive.returncode != 0:
        return f"exporting {rev}: {_failed('git archive', archive)}"
    unpack = subprocess.run(
        ["tar", "-x", "-C", str(dest)],
        input=archive.stdout,
        env=foreign_repo_env(),
        capture_output=True,
        check=False,
    )
    if unpack.returncode != 0:
        return f"exporting {rev}: {_failed('tar -x', unpack)}"
    (dest / ".goh-head").write_text(f"{sha}\n")
    return None


def _failed(what: str, r: subprocess.CompletedProcess) -> str:
    """A failed step, never an empty reason: its exit (a signal by name) and its stderr's tail."""
    why = r.stderr.decode("utf-8", "replace").strip() if isinstance(r.stderr, bytes) else r.stderr
    code = f"signal {-r.returncode}" if r.returncode < 0 else f"exit {r.returncode}"
    tail = " | ".join((why or "").strip().splitlines()[-3:])
    return f"{what} {code}: {tail or 'no stderr'}"


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
    return env


def parse(rc: int, out: str) -> Verdict:
    """One `goh structural --full --keep-going` run, step by step. An exit that is not 0 or 1, or
    one its output does not account for, is an ERROR -- never read as green."""
    v, step = Verdict(), None
    for line in out.splitlines():
        if m := STEP_HEAD.match(line):
            step = m.group(1)
        elif m := STEP_RED.match(line):
            v.red.add(m.group(1))
        elif (m := STEP_OK.match(line)) and m.group(1) != "all structural gates passed":
            v.green.add(m.group(1))
        elif FOUND.match(line) and step:
            v.found.setdefault(step, []).append(line[1:].strip())
    tail = " | ".join(out.strip().splitlines()[-3:])
    if rc not in (0, 1):
        v.error = f"exit {rc}: {tail}"
    elif rc == 1 and not v.red:
        v.error = f"exit 1 naming no failed step: {tail}"
    elif rc == 0 and (v.red or not v.green):
        v.error = f"exit 0 over {len(v.red)} red and {len(v.green)} green step(s): {tail}"
    return v


def structural(side: Side, tree: Path) -> Verdict:
    """One side's native gates over one consumer, in a session of its own: a run past its
    ceiling is killed with everything it spawned (shellcheck, the probes), never left running."""
    try:
        proc = subprocess.Popen(
            [str(side.bin), "structural", "--full", "--keep-going"],
            cwd=tree,
            env=_env(side),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
        )
    except OSError as e:
        return Verdict(error=str(e))
    try:
        out, _ = proc.communicate(timeout=RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        killtree.stop_group(
            proc.pid, proc=proc
        )  # its own session: the group id is its pid; TERM, then KILL
        proc.communicate()
        return Verdict(error=f"past its {RUN_TIMEOUT_S} s ceiling, killed")
    return parse(proc.returncode, out)


def remedy(step: str, found: list[str]) -> str:
    seed = SEEDS.get(step)
    if seed and not any(": error:" in f for f in found):
        return f"run `{seed}` in the repo and commit the seed (or fix the findings)"
    return "fix the findings (`goh structural --full --keep-going` in the repo lists them)"


def judge(repo: Path, base: Side, head: Side, scratch: Path) -> dict:
    """{turned_red: [...], already_red: [...], errors: [...]} for one consumer."""
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
    _git(tree, "reset", "-q", "--hard")
    _git(tree, "clean", "-q", "-fdx")
    now = structural(head, tree)
    if now.error:
        return {"errors": [f"under {head.label}: {now.error}"]}
    # A base that could not judge (a .gatesrc key it predates, say) proves no step green: every red
    # under HEAD then counts as this release's, the direction that refuses.
    before = None if was.error else was.red
    out: dict = {"turned_red": [], "already_red": [], "errors": []}
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
    return out


def _where(repo: Path, root: Path) -> str:
    try:
        return str(repo.relative_to(root))
    except ValueError:
        return str(repo)


def sweep(repos: list[Path], root: Path, base: Side, head: Side, jobs: int) -> dict:
    turned, already, errors = {}, {}, []
    with (
        tempfile.TemporaryDirectory(prefix="goh-release-canary-") as tmp,
        cf.ThreadPoolExecutor(max_workers=jobs) as pool,
    ):
        futs = {
            pool.submit(judge, r, base, head, Path(tmp) / str(i)): r for i, r in enumerate(repos)
        }
        for fut in cf.as_completed(futs):
            where, got = _where(futs[fut], root), fut.result()
            if got.get("turned_red"):
                turned[where] = got["turned_red"]
            if got.get("already_red"):
                already[where] = got["already_red"]
            errors += [f"{where}: {e}" for e in got.get("errors", [])]
    return {"turned_red": turned, "already_red": already, "errors": sorted(errors)}


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
        rec.update(sweep(repos, args.root, base, head, args.jobs))
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
        n = len(rec["turned_red"])
        err(f"release canary: {head} turns {n} consumer(s) red at push that {base} left green:")
        for where, flips in sorted(rec["turned_red"].items()):
            for f in flips:
                err(f"  {where}: {f['step']} -- {f['remedy']}")
                for line in f["found"]:
                    info(f"      found: {line}")
    if rec["verdict"] == "refused":
        err("release canary: REFUSED -- fix or seed each consumer above (in that repo), then land")
        err("  again; a consumer that should leave the estate leaves its registry, by name")
    else:
        ok(f"release canary: {rec['consumers']} consumer(s), none turned red by {head} over {base}")


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
    args = ap.parse_args()
    args.root, args.goh = args.root.resolve(), args.goh.resolve()
    rec, rc = run(args)
    estate_registry.update_state(args.state, lambda old: {**old, KEY: rec})
    say(rec)
    return rc


if __name__ == "__main__":
    sys.exit(main())
