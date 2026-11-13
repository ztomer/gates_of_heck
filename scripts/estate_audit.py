#!/usr/bin/env python3
"""estate_audit.py -- the house Rust audit over every consumer, on a schedule (BACKLOG 6.13).

RUSTSEC-2024-0449 turned antiknob's and media_server's pushes red at the same moment with no code
change: the gate working as designed, found at the worst time. This runs rust_gate.sh's advisory
step (`cargo audit`, biting) over every tracked Cargo.lock under the estate root, from each
workspace root so its own `.cargo/audit.toml` exemptions apply, and records what fires in one
state file. hooks/claude/estate_advisories.py reads that file at the start of every Claude
session -- where the owner already looks -- and says so when the file is missing or old, because
a job that stopped running reads exactly like a clean estate.

Only repos the house gate runs in are audited: a work tree with a `.gatesrc` at its root. A
third-party clone kept for reference or vendoring has none, no push of its is red, and the state
file names every repo set aside for that (`ungated`) so a house repo that lost its `.gatesrc` is
visible instead of silently unaudited.

The verdict is the one the repo's PUSH would reach (queue GOH.EA2): `lib/gate_coverage.py` reads
which locks the repo's gate audits and with which tool. A lock a step audits is judged by that
step -- cargo-audit with its `.cargo/audit.toml` and deny flags, cargo-deny RUN with its own
`deny.toml` -- and is the only kind called red. A tracked lock no step audits is `unaudited`, "not
audited at push" (its push is never red; the gap is the finding), and one a step goh cannot read
might audit is `undetermined`.

The estate is the root UNIONED with the registry `install.sh` writes (BACKLOG 6.14): a consumer
that lives outside the root (`~/.claude/skills`) is audited too, and a registered path that is
gone is named, never skipped.

    python3 scripts/estate_audit.py                 # audit ~/Projects, record, print
    python3 scripts/estate_audit.py --install       # write the daily launchd job (macOS)

GOH_ESTATE_ROOT and GOH_ESTATE_STATE override the root and the state file; the registry lives in
the state file's directory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "checks"))
from _gitutil import foreign_repo_env

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tui.lib import counted, info, noun, ok, warn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import advisory_db
import estate_registry
import gate_coverage
import hook_currency

DENY = [
    "--deny",
    "warnings",
    "--deny",
    "unmaintained",
    "--deny",
    "unsound",
    "--deny",
    "yanked",
]
REPO_DEPTH = estate_registry.REPO_DEPTH  # ~/Projects/<repo>, ~/Projects/<group>/<repo>
AUDIT_TIMEOUT_S = 300  # cargo-audit's own database lock waits up to 300 s
LABEL = "com.ztomer.goh-estate-audit"
RUN_HOUR = 9
# Keys another writer owns in the same state file (scripts/release_canary.py).
KEPT_KEYS = ("release_canary",)


def default_state() -> Path:
    """Where the last run is recorded. `GOH_ESTATE_STATE` names the file, and its DIRECTORY
    holds the estate registry (BACKLOG 6.14): the audit and the registry live together, so a test
    that names its own state file gets its own registry."""
    return estate_registry.state_file()


def repos(root: Path) -> list[Path]:
    """Every git work tree at most REPO_DEPTH below the root, nested ones included: a repository
    inside another's tree (servers/media_server) is not tracked by its parent, so the walk goes on
    below a repository rather than stopping at it."""
    return estate_registry.walk_repos(root, estate_registry.REPO_DEPTH)


def gated(repo: Path) -> bool:
    """Whether the house gate runs in this repo: `install.sh` writes a `.gatesrc` into every one it
    wires goh into, so a work tree without one is a third-party clone kept for reference or
    vendoring. No push of its is red, and what a vendored path dependency contributes to a house
    build is already in the CONSUMER's own lock, which is audited. (The enclosing repo's ignore
    list is no discriminator: servers/media_server is ignored by servers/ and is a house repo.)"""
    return (repo / ".gatesrc").is_file()


def excluded(repo: Path) -> Callable[[str], object]:
    """The repo's own GOH_EXCLUDE, read the way every gate reads it (by sourcing `.gatesrc`) and
    applied as `rust_gate.sh --each-crate` applies it: `re.search` on the tracked path. A vendored
    or reference tree the repo declared not its own is not its advisory either."""
    r = subprocess.run(
        [
            "bash",
            "-c",
            'unset GOH_EXCLUDE; [ -f .gatesrc ] && . ./.gatesrc >/dev/null 2>&1; printf %s "${GOH_EXCLUDE:-}"',
        ],
        cwd=repo,
        env=foreign_repo_env(),  # the repo's own config, never a calling hook's repository
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    try:
        return re.compile(r.stdout).search if r.stdout else lambda _: None
    except re.error:
        return lambda _: None  # a broken pattern excludes nothing: over-report, never hide


def lockfiles(repo: Path) -> tuple[list[Path], int]:
    """(tracked lockfiles the repo gates, how many its GOH_EXCLUDE set aside)."""
    r = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "--", "Cargo.lock", "**/Cargo.lock"],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    tracked = [p for p in r.stdout.split("\0") if p] if r.returncode == 0 else []
    skip = excluded(repo) if tracked else (lambda _: None)
    kept = [p for p in tracked if not skip(p)]
    return [repo / p for p in kept], len(tracked) - len(kept)


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=foreign_repo_env(),  # each repo's own git, never a calling hook's
        capture_output=True,
        text=True,
        timeout=AUDIT_TIMEOUT_S,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def audit(
    lock: Path, db: str | None, fetch: bool, cwd: Path | None = None
) -> tuple[list[dict], str | None]:
    """(findings, error): each finding {id, kind, crate, title}, every kind cargo-audit reports,
    run from `cwd` (default: the lockfile's own workspace root) so ITS `.cargo/audit.toml` applies."""
    argv = ["cargo", "audit", "--json", *DENY, "--file", str(lock)]
    where = cwd or lock.parent
    try:
        if db:  # a fixture: no lock, nothing of the real database touched
            r = _run([*argv, "--db", db, "--stale", *([] if fetch else ["--no-fetch"])], where)
        else:
            with advisory_db.held(fetch) as shared:
                r = _run([*argv, *advisory_db.audit_args(shared, fetch)], where)
        report = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [], str(e)
    except json.JSONDecodeError:
        return [], (r.stderr.strip().splitlines() or [f"exit {r.returncode}"])[-1]
    found = []
    for v in report.get("vulnerabilities", {}).get("list", []):
        found.append(_finding(v, "vulnerability"))
    for kind, warnings in report.get("warnings", {}).items():
        found += [_finding(w, kind) for w in warnings if w.get("advisory")]
    return found, None


def deny(
    repo: Path, a: gate_coverage.Audit, cargo_deny: list[str], fetch: bool
) -> tuple[list[dict], str | None]:
    """(the findings that FAIL the repo's own cargo-deny step, error): cargo-deny is RUN with the
    step's own arguments from the step's directory, so its `deny.toml` -- ignore list, graph
    targets, what it calls unmaintained -- decides, never an imitation of it. Only an error-severity
    advisory diagnostic fails the step; a note (an ignored advisory) does not."""
    argv = [*cargo_deny, "--format", "json", *([] if fetch else ["--offline"]), *a.args]
    try:
        r = _run(argv, repo / a.cwd)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [], str(e)
    found, summary = [], False
    for line in r.stderr.splitlines() + r.stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        summary = summary or d.get("type") == "summary"
        f = d.get("fields") or {}
        adv = f.get("advisory")
        if d.get("type") != "diagnostic" or f.get("severity") != "error" or not adv:
            continue
        krate = (f.get("graphs") or [{}])[0].get("Krate") or {}
        found.append(
            {
                "id": adv.get("id", "?"),
                "kind": f.get("code", "advisory"),
                "crate": f"{krate.get('name', adv.get('package', '?'))} {krate.get('version', '')}".strip(),
                "title": adv.get("title", f.get("message", "")),
            }
        )
    if not summary and r.returncode != 0:
        return found, (r.stderr.strip().splitlines() or [f"cargo deny exit {r.returncode}"])[-1]
    return found, None


def push_findings(
    repo: Path, audits: list, db: str | None, fetch: bool, cargo_deny: list[str]
) -> tuple[list[dict], list[str]]:
    """What the repo's push would fail on, for one lockfile: every step that audits it, each judged
    the way that step judges (a step failing is a red push, so the steps UNION)."""
    found, errors = [], []
    for a in audits:
        if a.tool == "cargo-deny":
            got, error = deny(repo, a, cargo_deny, fetch)
        else:
            kinds = gate_coverage.denied_kinds(repo, a)
            got, error = audit(repo / a.lock, db, fetch, repo / a.cwd)
            got = [f for f in got if f["kind"] == "vulnerability" or f["kind"] in kinds]
        if error:
            errors.append(f"{a.tool}: {error}")
        found += [f for f in got if f not in found]
    return found, errors


def _finding(entry: dict, kind: str) -> dict:
    adv, pkg = entry["advisory"], entry.get("package", {})
    return {
        "id": adv["id"],
        "kind": kind,
        "crate": f"{pkg.get('name', adv.get('package', '?'))} {pkg.get('version', '')}".strip(),
        "title": adv.get("title", ""),
    }


def _where(lock: Path, root: Path) -> str:
    """The lockfile's name in the report: relative to the estate root when it is under it, and its
    OWN path when it is not. A repo outside the root is in the estate (BACKLOG 6.14), and naming
    it by a path relative to a root it does not live under would name nothing."""
    try:
        return str(lock.relative_to(root))
    except ValueError:
        return str(lock)


def _firing(lock: Path, db: str | None, fetch: bool) -> tuple[list[str], str | None]:
    """The advisories that fire against a lock no push audits, with the house deny flags."""
    found, error = audit(lock, db, fetch)
    return sorted({f["id"] for f in found}), error


def run(
    root: Path, state: Path, db: str | None, fetch: bool, cargo_deny: list[str] | None = None
) -> dict:
    # The FIRST run has nothing to compare with: what it finds is the standing estate, not news, so
    # its findings carry no first_seen and the session hook does not call them NEW. "First" is the
    # first AUDIT, not the file's first write: the release canary records into this file too
    # (scripts/release_canary.py), and a file it made holds no audit to compare with.
    try:
        old = json.loads(state.read_text())
    except (OSError, ValueError):
        old = {}
    baseline = not isinstance(old, dict) or "ran_at" not in old
    previous = {} if baseline else old.get("advisories", {})
    cargo_deny = cargo_deny or ["cargo", "deny"]
    now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    advisories: dict[str, dict] = {}
    errors, audited, set_aside, ungated = [], 0, 0, []
    unaudited: dict[str, list[str]] = {}
    undetermined: dict[str, dict] = {}
    # The estate is the walk UNIONED with the registry (BACKLOG 6.14): a repo `install.sh` wired
    # goh into outside the root is a consumer, and a registered path that is gone is named.
    estate, problems = estate_registry.union(repos(root), estate_registry.registry_path(state))
    stale_hooks: dict[str, list[str]] = {}
    goh = Path(__file__).resolve().parent.parent
    for repo in estate:
        # Stock hooks older than goh's (GOH.HOOKREFRESH): named here, refreshed by `goh hooks
        # --refresh`. Every repo, gated or not -- a stale copy refuses commits either way.
        if hooks := hook_currency.stale(repo, goh, foreign_repo_env()):
            stale_hooks[_where(repo, root)] = hooks
        if not gated(repo):
            # NAMED, not dropped: a house repo that lost its .gatesrc must read as set aside, not
            # as an estate that happens to be clean.
            ungated.append(_where(repo, root))
            continue
        locks, aside = lockfiles(repo)
        set_aside += aside
        # What the repo's PUSH audits, read from its gate (lib/gate_coverage.py): the verdict is
        # the one that push would reach, under the tool and the policy the push uses.
        cov = gate_coverage.coverage(repo, foreign_repo_env()) if locks else None
        for lock in locks:
            rel = lock.relative_to(repo).as_posix()
            where = _where(lock, root)
            steps = [a for a in cov.audits if a.lock == rel] if cov else []
            if not steps:
                # No gate step audits it: never "red at next push" -- the push never looks. NAMED
                # as the gap it is, with what fires there unseen; `undetermined` when a step goh
                # cannot read (a shell-variable target) might be the one that does.
                ids, error = _firing(lock, db, fetch)
                if error:
                    errors.append(f"{where}: {error}")
                if cov and cov.unresolved:
                    undetermined[where] = {"firing": ids, "unread": cov.unresolved[:3]}
                else:
                    unaudited[where] = ids
                continue
            audited += 1
            found, errs = push_findings(repo, steps, db, fetch, cargo_deny)
            errors += [f"{where}: {e}" for e in errs]
            for f in found:
                a = advisories.setdefault(
                    f["id"],
                    {
                        "kind": f["kind"],
                        "title": f["title"],
                        "crates": [],
                        "lockfiles": [],
                        "first_seen": previous.get(f["id"], {}).get(
                            "first_seen", None if baseline else now
                        ),
                    },
                )
                if f["crate"] not in a["crates"]:
                    a["crates"].append(f["crate"])
                if where not in a["lockfiles"]:
                    a["lockfiles"].append(where)
    result = {
        "ran_at": now,
        "root": str(root),
        "lockfiles": audited,
        "excluded": set_aside,
        "ungated": sorted(ungated),
        "advisories": advisories,
        "unaudited": unaudited,
        "undetermined": undetermined,
        "stale_hooks": stale_hooks,
        "errors": errors,
        "registry": str(estate_registry.registry_path(state)),
        "problems": problems,
    }
    # The audit owns its keys and nothing else: the release canary's record in the same file is
    # kept (lib/estate_registry.py update_state), so the daily run never erases a refusal.
    estate_registry.update_state(
        state, lambda o: {**{k: v for k, v in o.items() if k in KEPT_KEYS}, **result}
    )
    return result


def plist(python: str, script: Path, log: Path) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{python}</string><string>{script}</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>{RUN_HOUR}</integer><key>Minute</key><integer>0</integer></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>{Path.home() / ".cargo" / "bin"}:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
</dict>
</plist>
"""


def install(state: Path) -> int:
    target = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    log = state.with_suffix(".log")
    target.write_text(plist(sys.executable, Path(__file__).resolve(), log))
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}", str(target)],
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    r = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if r.returncode != 0:
        warn(f"launchctl bootstrap {target}: {r.stderr.strip()}")
        return 1
    ok(f"daily at {RUN_HOUR:02d}:00 and at login: {target} (log {log})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("GOH_ESTATE_ROOT", Path.home() / "Projects")),
    )
    ap.add_argument("--state", type=Path, default=default_state())
    ap.add_argument("--db", help="advisory database path (a fixture, in tests)")
    ap.add_argument("--no-fetch", action="store_true", help="judge against the cached database")
    ap.add_argument(
        "--cargo-deny",
        default="cargo deny",
        help="the cargo-deny command a repo's `cargo deny` step runs (a fixture, in tests)",
    )
    ap.add_argument("--install", action="store_true", help="write and load the daily launchd job")
    args = ap.parse_args()
    if args.install:
        return install(args.state)
    result = run(
        args.root.resolve(), args.state, args.db, not args.no_fetch, shlex.split(args.cargo_deny)
    )
    info(
        f"{counted(result['lockfiles'], 'lockfile')} under {result['root']},"
        f" {result['excluded']} set aside by their repo's GOH_EXCLUDE,"
        f" {counted(len(result['ungated']), 'repo')} set aside for no .gatesrc"
        f" -> {args.state}"
    )
    for aid, a in sorted(result["advisories"].items()):
        warn(f"{aid} ({a['kind']}) {', '.join(a['crates'])}: {', '.join(a['lockfiles'])}")
    for where, ids in sorted(result["unaudited"].items()):
        warn(
            f"NOT AUDITED AT PUSH (no gate step of its repo audits it): {where}"
            + (f" -- {', '.join(ids)} fire there" if ids else "")
        )
    for where, u in sorted(result["undetermined"].items()):
        warn(
            f"push audit not readable: {where} ({'; '.join(u['unread'])})"
            + (f" -- {', '.join(u['firing'])} fire there" if u["firing"] else "")
        )
    for where, hooks in sorted(result["stale_hooks"].items()):
        warn(
            f"stale stock {noun(len(hooks), 'hook')} {', '.join(hooks)}: {where}"
            " -- refresh: goh hooks --refresh"
        )
    for e in result["errors"]:
        warn(f"not audited: {e}")
    # A registered path that is gone is NAMED (BACKLOG 6.14): a consumer that left the estate
    # must be visible, not a line quietly dropped from the count.
    for p in result["problems"]:
        warn(f"estate registry: {p}")
    if not result["advisories"] and not result["errors"]:
        ok("no advisory fires anywhere in the estate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
