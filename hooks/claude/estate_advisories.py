#!/usr/bin/env python3
"""A Claude Code SessionStart hook: what the scheduled estate audit found (BACKLOG 6.13).

scripts/estate_audit.py records every advisory that fires against any tracked Cargo.lock in the
estate. A report nobody reads is the old state with extra steps, so this puts it where the owner
already looks: SessionStart stdout is added to every session's context. It prints NOTHING when the
latest run is fresh and clean, one line per firing advisory otherwise, and -- because a job that
stopped running reads exactly like a clean estate -- says so when the state file is missing or
older than STALE_AFTER_H (tests/test_estate_audit.py). It names, in one line, the repos the audit set
aside for having no `.gatesrc`. And it says what the last RELEASE CANARY found (GOH.CANARY,
scripts/release_canary.py): a goh change that turns a consumer red at push, refused at land and
named here, by repo and the seed it needs, until a run clears it (tests/test_release_canary.py).
And it names a reuse path the reuse audit turned off whose fault item was never filed
(GOH.REUSEAUDITFILE, tests/test_reuse_audit.py). And it names every directory under goh's state
root written per run with no bound (GOH.LANDLOGPRUNE, lib/state_bounds.py,
tests/test_state_bounds.py). And it names every live load run (spinners burning now) with its why
and its owner's directory (GOH.LOADWHY, lib/load_live.py, tests/test_load_why.py).

It also names every registered estate repo with commits AHEAD of its upstream (GOH.AHEADREPORT):
a commit landed in a repo other than the item's own -- a learning written into ~/.claude/skills from
another repo's session -- is pushed by nobody, since `goh ship`, `loopctl done` and the agents all
push only the item's repo, and skills reached 17 commits ahead unseen. Asked locally (`git rev-list
--count @{u}..HEAD`: no fetch, no network), every repo at once, each bounded by AHEAD_TIMEOUT_S; a
repo with no upstream, or one that does not answer in time, says nothing (tests/test_estate_ahead.py).

The state is a day old by design, and a lock can move on in that day (GOH.EA3: a private consumer's pulldown-cmark
0.13.4 -> 0.13.6 landed after the run, and this hook still said the next push was red). So before
any lock is called red, the lock is RE-READ as it is now: one that no longer carries any crate
version the run named is said to be "fixed since the run", never red. A lock that is gone or will
not parse proves nothing, so it keeps the run's verdict -- it is never reported fixed.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the goh root: tui.lib
from tui.lib import counted

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # siblings, run or loaded by path
import estate_registry  # the registry, beside the audit's state file
import gitrun  # git under a deadline that never strands an index.lock

sys.path.insert(0, str(Path(__file__).resolve().parent))  # its siblings, whoever imports it
from load_runs import lines as load_run_lines  # live spinner runs and their why (GOH.LOADWHY)

STALE_AFTER_H = 36  # a daily job, plus a missed run's grace
NEW_WITHIN_H = 48
MAX_LINES = 12
MAX_REPOS = 6
SCRIPT = "$GOH_DIR/scripts/estate_audit.py"
AHEAD_TIMEOUT_S = 2.0  # one local rev-list; a repo slower than this is not worth a SessionStart
AHEAD_WORKERS = 16


def state_path() -> Path:
    """The estate state's name (lib/estate_registry.py `state_file`): its record is goh.db's."""
    return estate_registry.state_file()


def _age_h(stamp: str, now: dt.datetime) -> float | None:
    try:
        return (now - dt.datetime.fromisoformat(stamp)).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def _carries(lock: Path, crates: list[str]) -> bool:
    """Whether `lock` still holds any of `crates` ("name version", as the run recorded them).
    True when the lock cannot be read: only a lock read and found clean is evidence of a fix."""
    try:
        pkgs = tomllib.loads(lock.read_text()).get("package", [])
    except (OSError, ValueError):
        return True
    have = {f"{p.get('name')} {p.get('version')}" for p in pkgs if isinstance(p, dict)}
    return not crates or any(c in have for c in crates)


def _recheck(advisories: dict, root: str | None) -> tuple[dict, list[tuple[str, list[str]]]]:
    """(the advisories with each lock re-read now, [(id, locks fixed since the run)]). A lock is
    named relative to the run's estate root, or by its own path when it lives outside it."""
    still, fixed = {}, []
    base = Path(root) if root else Path.cwd()
    for aid, a in advisories.items():
        locks = a.get("lockfiles", [])
        gone = [lf for lf in locks if not _carries(base / lf, a.get("crates", []))]
        if gone:
            fixed.append((aid, gone))
        if len(gone) < len(locks):
            still[aid] = {**a, "lockfiles": [lf for lf in locks if lf not in gone]}
    return still, fixed


def _canary(rec: dict) -> list[str]:
    """What the last release canary found (scripts/release_canary.py): a goh change that turns a
    consumer red at push is refused at land, and the refusal is said here until a run clears it."""
    lines, when = [], f"{rec.get('head', 'HEAD')} over {rec.get('base', '?')}, {rec.get('ran_at')}"
    turned = rec.get("turned_red") or {}
    if turned:
        named = [
            f"{repo} ({f['step']}: {f['remedy']})"
            for repo, flips in sorted(turned.items())
            for f in flips
        ]
        lines.append(
            f"goh release canary REFUSED ({when}): it turns {counted(len(turned), 'consumer')}"
            " red at push"
            f" -- {'; '.join(named[:MAX_REPOS])}{'; ...' if len(named) > MAX_REPOS else ''}"
        )
    errors = rec.get("errors") or []
    if errors:
        lines.append(
            f"goh release canary could not judge ({when}): {'; '.join(errors[:3])}"
            f"{'; ...' if len(errors) > 3 else ''}"
        )
    already = rec.get("already_red") or {}
    if already:
        named = [f"{repo} ({', '.join(steps)})" for repo, steps in sorted(already.items())]
        lines.append(
            f"goh release canary: {counted(len(already), 'consumer')} red at push under the last"
            " release"
            f" too (their own debt): {', '.join(named[:MAX_REPOS])}"
            f"{', ...' if len(named) > MAX_REPOS else ''}"
        )
    grace = rec.get("grace_over") or {}  # named; refused within 7 days of it (GOH.CANARYMISS)
    if grace.get("turned_red"):
        named = [
            f"{repo} ({', '.join(f['step'] for f in flips)})"
            for repo, flips in sorted(grace["turned_red"].items())
        ]
        how = (
            "REFUSED, it ends within 7 days" if grace.get("binding") else "seed or fix before then"
        )
        lines.append(
            f"goh release canary: the grace end ({grace.get('over_from') or '?'}) turns"
            f" {counted(len(named), 'consumer')} red at push -- {how}:"
            f" {'; '.join(named[:MAX_REPOS])}{'; ...' if len(named) > MAX_REPOS else ''}"
        )
    return lines


def _detail(found: list[str]) -> str:
    """Each undeclared floor as the consumer writes it (KEY=N), else the step's first finding."""
    keys = [f for f in found if re.fullmatch(r"GOH_\w+_MIN_FILES=\d+", f)]
    return ", ".join(keys) if keys else found[0].strip()


def _watch(rec: dict) -> list[str]:
    """What the scheduled grace-end pass found (scripts/grace_watch.py, GOH.GRACEWATCH): a grace
    end within 7 days that turns a consumer red, with no release needed for it to happen."""
    when = rec.get("over_from") or "?"
    lines = []
    if rec.get("errors"):
        lines.append(
            f"goh grace watch could not judge at the grace end ({when}):"
            f" {'; '.join(rec['errors'][:3])}{'; ...' if len(rec['errors']) > 3 else ''}"
        )
    turned = rec.get("turned_red") or {}
    if turned:
        named = [
            f"{repo} ({f['step']}{': ' + _detail(f.get('found') or []) if f.get('found') else ''})"
            for repo, flips in sorted(turned.items())
            for f in flips
        ]
        lines.append(
            f"goh grace watch: the grace end ({when}) turns {counted(len(turned), 'consumer')} red"
            f" at push, release or not -- seed or fix before then:"
            f" {'; '.join(named[:MAX_REPOS])}{'; ...' if len(named) > MAX_REPOS else ''}"
        )
    return lines


def _ahead(repo: Path) -> int:
    """Commits on HEAD its upstream lacks, as git knows it locally; 0 with no upstream, or when git
    does not answer in time. The hook's own git variables are dropped: the repo is named by path."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = gitrun.run_reader(
            ["rev-list", "--count", "@{upstream}..HEAD"],
            repo=repo,
            timeout=AHEAD_TIMEOUT_S,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    out = r.stdout.strip()
    return int(out) if r.returncode == 0 and out.isdigit() else 0


def _named(repo: Path) -> str:
    home = Path.home()
    return f"~/{repo.relative_to(home)}" if repo.is_relative_to(home) else str(repo)


def ahead_lines(registry: Path) -> list[str]:
    """One line per registered repo with unpushed commits, in path order."""
    repos = estate_registry.existing(registry)
    with ThreadPoolExecutor(max_workers=AHEAD_WORKERS) as pool:
        counts = list(pool.map(_ahead, repos))
    ahead = [(r, n) for r, n in zip(repos, counts, strict=True) if n > 0]
    lines = [
        f"estate: {_named(r)} is {counted(n, 'commit')} ahead of its upstream -- unpushed; push it"
        for r, n in ahead[:MAX_LINES]
    ]
    if len(ahead) > MAX_LINES:
        rest = ", ".join(_named(r) for r, _ in ahead[MAX_LINES:])
        lines.append(f"estate: {counted(len(ahead) - MAX_LINES, 'more repo')} ahead: {rest}")
    return lines


def reuse_lines() -> list[str]:
    """A reuse path the audit turned off whose fault item was NOT filed (loopctl refused it, or no
    loopctl answered): the record alone alerts nobody, so every session is told (GOH.REUSEAUDITFILE,
    lib/reuse_audit.py unfiled)."""
    try:
        import reuse_audit  # lib/, on the path above; its store is GOH_REUSE_DIR's
    except ImportError:
        return []
    try:
        off = reuse_audit.unfiled()
    except (OSError, ValueError):
        return []
    return [f"✗ goh reuse audit: {line} -- file it by hand, or `reuse_audit.py report`"
            for line in off]  # fmt: skip


def state_lines(state: Path) -> list[str]:
    """Each directory under goh's state root (the one STATE lives in) written per run with no bound:
    a pile nobody prunes grows until the disk says so (GOH.LANDLOGPRUNE, lib/state_bounds.py)."""
    try:
        import state_bounds  # lib/, on the path above
    except ImportError:
        return []
    return [f"✗ goh state: {line} -- bound it in lib/state_bounds.py REGISTRY, or excuse it there"
            for line in state_bounds.unbounded(state.parent)]  # fmt: skip


def report(state: Path, now: dt.datetime) -> list[str]:
    reg = estate_registry.registry_path(state)
    return (
        _audit(state, now)
        + ahead_lines(reg)
        + reuse_lines()
        + state_lines(state)
        + load_run_lines()
    )


def _audit(state: Path, now: dt.datetime) -> list[str]:
    data = estate_registry.read_state(state)  # goh.db's row, else the file before its import
    if not data and state.is_file():  # no row, and a file that read as nothing
        return [f"goh estate audit: {state} is unreadable; re-run `python3 {SCRIPT}`."]
    if not data:
        return [
            f"goh estate audit has never run here: `python3 {SCRIPT} --install` schedules it daily."
        ]
    lines = []
    age = _age_h(data.get("ran_at"), now)
    if age is None or age > STALE_AFTER_H:
        ran = data.get("ran_at", "never")
        lines.append(
            f"goh estate audit is STALE (last run {ran}): the scheduled job is not running; "
            f"`python3 {SCRIPT} --install`."
        )
    lines += _canary(data.get("release_canary") or {})
    lines += _watch(data.get("grace_watch") or {})
    advisories, fixed = _recheck(data.get("advisories", {}), data.get("root"))
    new, standing = [], []
    for aid, a in sorted(advisories.items()):
        seen = _age_h(a.get("first_seen"), now)
        (new if seen is not None and seen <= NEW_WITHIN_H else standing).append((aid, a))
    for aid, a in new:
        lines.append(
            f"goh estate audit: NEW {aid} ({a.get('kind')}) {', '.join(a.get('crates', []))}"
            f" -- the next push of {', '.join(a.get('lockfiles', []))} is red"
        )
    for aid, gone in fixed:
        lines.append(
            f"goh estate audit: {aid} fixed since the run ({data.get('ran_at')}) in"
            f" {', '.join(gone)}: the lock no longer carries the named version"
        )
    if standing:
        repos = sorted(
            {lf.split("/Cargo.lock")[0] for _, a in standing for lf in a.get("lockfiles", [])}
        )
        lines.append(
            f"goh estate audit: {counted(len(standing), 'standing advisory', 'standing advisories')}"
            f" in {counted(len(repos), 'lockfile')}"
            f" ({', '.join(repos[:MAX_REPOS])}{', ...' if len(repos) > MAX_REPOS else ''});"
            f" `python3 {SCRIPT} --no-fetch` lists them"
        )
    # A repo with no .gatesrc is not gated, so none of its pushes is red and the audit set it
    # aside (scripts/estate_audit.py); one line says how many and why, so a house repo that lost
    # its .gatesrc is visible rather than silently unaudited.
    ungated = data.get("ungated", [])
    if ungated:
        lines.append(
            f"goh estate audit: {counted(len(ungated), 'repo')} set aside as ungated (no .gatesrc,"
            " so no house"
            f" gate runs there and no push of theirs is red): {', '.join(ungated[:MAX_REPOS])}"
            f"{', ...' if len(ungated) > MAX_REPOS else ''}"
        )
    # A lock no gate step of its repo audits is NOT red at the next push -- the push never looks
    # at it -- and that is the finding: an ungated lock (lib/gate_coverage.py). What fires there
    # is said too, because nobody else will see it.
    unaudited = data.get("unaudited", {})
    if unaudited:
        firing = sorted({i for ids in unaudited.values() for i in ids})
        lines.append(
            f"goh estate audit: {counted(len(unaudited), 'tracked lockfile')} NOT AUDITED AT PUSH"
            " (no gate"
            f" step of their repo audits them): {', '.join(sorted(unaudited)[:MAX_REPOS])}"
            f"{', ...' if len(unaudited) > MAX_REPOS else ''}"
            + (
                f"; {counted(len(firing), 'advisory', 'advisories')} fire there unseen"
                if firing
                else ""
            )
        )
    undetermined = data.get("undetermined", {})
    if undetermined:
        lines.append(
            f"goh estate audit: {counted(len(undetermined), 'lockfile')} whose push-time audit goh"
            " cannot"
            f" read (a gate step names its target through a shell variable):"
            f" {', '.join(sorted(undetermined)[:MAX_REPOS])}"
            f"{', ...' if len(undetermined) > MAX_REPOS else ''}"
        )
    # Stock hooks older than goh's refuse every commit there (structural.sh); one command
    # refreshes them all (scripts/hooks_refresh.py, GOH.HOOKREFRESH).
    stale_hooks = data.get("stale_hooks", {})
    if stale_hooks:
        lines.append(
            f"goh estate audit: {counted(len(stale_hooks), 'repo')} with stale stock hooks (their"
            " commits"
            f" are refused): {', '.join(sorted(stale_hooks)[:MAX_REPOS])}"
            f"{', ...' if len(stale_hooks) > MAX_REPOS else ''} -- refresh: goh hooks --refresh"
        )
    for e in data.get("errors", []):
        lines.append(f"  not audited: {e}")
    # A registered path that is gone (BACKLOG 6.14): a consumer that left the estate, named
    # here because the owner reads this at the start of every session.
    for p in data.get("problems", []):
        lines.append(f"  estate registry: {p}")
    if len(lines) > MAX_LINES:
        lines = [
            *lines[: MAX_LINES - 1],
            f"  ... and {len(lines) - MAX_LINES + 1} more in {state}",
        ]
    return lines


def main() -> int:
    try:
        json.load(sys.stdin)
    except ValueError:
        pass
    for line in report(state_path(), dt.datetime.now(dt.UTC)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
