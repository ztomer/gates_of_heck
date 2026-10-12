#!/usr/bin/env python3
"""estate_audit.py -- the house Rust audit over every consumer, on a schedule (BACKLOG 6.13).

RUSTSEC-2024-0449 turned antiknob's and media_server's pushes red at the same moment with no code
change: the gate working as designed, found at the worst time. This runs rust_gate.sh's advisory
step (`cargo audit`, biting) over every tracked Cargo.lock under the estate root, from each
workspace root so its own `.cargo/audit.toml` exemptions apply, and records what fires in one
state file. hooks/claude/estate_advisories.py reads that file at the start of every Claude
session -- where the owner already looks -- and says so when the file is missing or old, because
a job that stopped running reads exactly like a clean estate.

    python3 scripts/estate_audit.py                 # audit ~/Projects, record, print
    python3 scripts/estate_audit.py --install       # write the daily launchd job (macOS)

GOH_ESTATE_ROOT and GOH_ESTATE_STATE override the root and the state file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "checks"))
from _gitutil import foreign_repo_env

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tui.lib import info, ok, warn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import advisory_db

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
REPO_DEPTH = 3  # ~/Projects/<repo>, ~/Projects/<group>/<repo>: servers/media_server, games/*
SKIP_DIRS = {"node_modules", "target", ".build", ".venv", "venv"}
AUDIT_TIMEOUT_S = 300  # cargo-audit's own database lock waits up to 300 s
LABEL = "com.ztomer.goh-estate-audit"
RUN_HOUR = 9


def default_state() -> Path:
    env = os.environ.get("GOH_ESTATE_STATE")
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return Path(env) if env else base / "goh" / "estate_audit.json"


def repos(root: Path) -> list[Path]:
    """Every git work tree at most REPO_DEPTH below the root, nested ones included: a repository
    inside another's tree (servers/media_server) is not tracked by its parent, so the walk goes on
    below a repository rather than stopping at it."""
    found, frontier = [], [(root, 0)]
    while frontier:
        d, depth = frontier.pop()
        if (d / ".git").exists():
            found.append(d)
        if depth == REPO_DEPTH:
            continue
        try:
            kids = [k for k in d.iterdir() if k.is_dir() and not k.is_symlink()]
        except OSError:
            continue
        frontier += [
            (k, depth + 1) for k in kids if k.name not in SKIP_DIRS and not k.name.startswith(".")
        ]
    return sorted(found)


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
    )
    tracked = [p for p in r.stdout.split("\0") if p] if r.returncode == 0 else []
    skip = excluded(repo) if tracked else (lambda _: None)
    kept = [p for p in tracked if not skip(p)]
    return [repo / p for p in kept], len(tracked) - len(kept)


def _run(argv: list[str], lock: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=lock.parent,
        env=foreign_repo_env(),  # each repo's own git, never a calling hook's
        capture_output=True,
        text=True,
        timeout=AUDIT_TIMEOUT_S,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def audit(lock: Path, db: str | None, fetch: bool) -> tuple[list[dict], str | None]:
    """(findings, error): each finding {id, kind, crate, title}, from the lockfile's own workspace
    root so its exemptions apply."""
    argv = ["cargo", "audit", "--json", *DENY, "--file", str(lock)]
    try:
        if db:  # a fixture: no lock, nothing of the real database touched
            r = _run([*argv, "--db", db, "--stale", *([] if fetch else ["--no-fetch"])], lock)
        else:
            with advisory_db.held(fetch) as shared:
                r = _run([*argv, *advisory_db.audit_args(shared, fetch)], lock)
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


def _finding(entry: dict, kind: str) -> dict:
    adv, pkg = entry["advisory"], entry.get("package", {})
    return {
        "id": adv["id"],
        "kind": kind,
        "crate": f"{pkg.get('name', adv.get('package', '?'))} {pkg.get('version', '')}".strip(),
        "title": adv.get("title", ""),
    }


def run(root: Path, state: Path, db: str | None, fetch: bool) -> dict:
    # The FIRST run has nothing to compare with: what it finds is the standing estate, not news, so
    # its findings carry no first_seen and the session hook does not call them NEW.
    previous, baseline = {}, not state.exists()
    if not baseline:
        try:
            previous = json.loads(state.read_text()).get("advisories", {})
        except (OSError, ValueError):
            previous = {}
    now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    advisories: dict[str, dict] = {}
    errors, audited, set_aside = [], 0, 0
    for repo in repos(root):
        locks, aside = lockfiles(repo)
        set_aside += aside
        for lock in locks:
            audited += 1
            found, error = audit(lock, db, fetch)
            where = str(lock.relative_to(root))
            if error:
                errors.append(f"{where}: {error}")
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
        "advisories": advisories,
        "errors": errors,
    }
    state.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=state.parent, delete=False, suffix=".tmp") as f:
        json.dump(result, f, indent=2, sort_keys=True)
    os.replace(f.name, state)
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
    )
    r = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)],
        capture_output=True,
        text=True,
        check=False,
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
    ap.add_argument("--install", action="store_true", help="write and load the daily launchd job")
    args = ap.parse_args()
    if args.install:
        return install(args.state)
    result = run(args.root.resolve(), args.state, args.db, not args.no_fetch)
    info(
        f"{result['lockfiles']} lockfile(s) under {result['root']}, {result['excluded']} set aside"
        f" by their repo's GOH_EXCLUDE -> {args.state}"
    )
    for aid, a in sorted(result["advisories"].items()):
        warn(f"{aid} ({a['kind']}) {', '.join(a['crates'])}: {', '.join(a['lockfiles'])}")
    for e in result["errors"]:
        warn(f"not audited: {e}")
    if not result["advisories"] and not result["errors"]:
        ok("no advisory fires anywhere in the estate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
