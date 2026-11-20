#!/usr/bin/env python3
"""check_audit_ignores.py -- every `cargo audit` exemption carries its reason and still fires
(BACKLOG 6.12; Rust rule 3: the ignore list is a ratchet).

cargo-audit accepts an ignore entry whose advisory no longer applies, silently (calibrated by
ztools, 2026-10-04: `ignore = ["RUSTSEC-2026-9999"]` passes clean), so a fixed advisory leaves an
exemption behind that would hide the next finding of that id. And nothing asks an entry why it
stands. This reads `.cargo/audit.toml` in the directory `rust_gate.sh` runs cargo in -- the only
place cargo-audit reads it -- and refuses:

  * an ignored id with no `# reason` on its own line;
  * an ignored id the lockfile no longer triggers: the audit is run again from a scratch
    directory, where no `audit.toml` is read and so nothing is ignored, and an id that is not
    among its findings is stale.

    python3 checks/check_audit_ignores.py [--no-fetch]                # from the workspace root
    python3 checks/check_audit_ignores.py --no-fetch --repo R [--lock L] # from a config's directory

    python3 checks/check_audit_ignores.py --shared [--estate ROOT] [--no-fetch]

`--shared` judges goh's own gates/shared_advisories.toml (GOH.SHAREDADVISORY) against the ESTATE,
not one repo: an entry is stale only when it fires in no tracked lock of any repo under ROOT
(default GOH_ESTATE_ROOT or ~/Projects, unioned with the estate registry), and one whose blocking
crate no longer reaches the advised crate in a lock carrying both is named as its re-open
condition met.

rust_gate.sh passes --no-fetch: its own audit step fetched the database a moment before. It audits
EVERY tracked lock of the repo (lib/rust_locks.py), and one `.cargo/audit.toml` may judge several
(a private server workspace's root config governs 31): with `--repo`, the locks are the ones
`rust_locks` says are audited from this directory, and an entry is stale only when it fires against
NONE of them. `--lock` adds the workspace lock the gate was pointed at, when git does not track it
yet.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from _goh_tmp import goh_tmp  # the ONE temp parent (GOH.FSCHURN3)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import advisory_db
import estate_registry
import rust_locks
import shared_advisories

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import foreign_repo_env

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import counted, err, ok

CONFIG = os.path.join(".cargo", "audit.toml")
ADVISORY = re.compile(r'"((?:RUSTSEC|GHSA|CVE)-[\w-]+)"')
REASON = re.compile(r"#\s*\S")


def ignore_lines(text: str) -> list[tuple[int, str, bool]]:
    """(line, id, has a reason) for each id in `[advisories] ignore`, read by line so the comment
    beside each one is still there to read."""
    out, inside, section = [], False, ""
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("[") and not inside:
            section = stripped
            continue
        if section == "[advisories]" and re.match(r"ignore\s*=", stripped):
            inside = True
        if inside:
            code = line.split("#", 1)[0]
            for m in ADVISORY.finditer(code):
                out.append((n, m.group(1), bool(REASON.search(line[len(code) :]))))
            if "]" in code:
                inside = False
    return out


def firing_any(locks: list[str], fetch: bool) -> set[str]:
    """Every advisory id any of `locks` triggers: the first read may fetch, the rest read it."""
    ids: set[str] = set()
    for i, lock in enumerate(locks):
        ids |= firing(lock, fetch=fetch and i == 0)
    return ids


def firing(lockfile: str, fetch: bool) -> set[str]:
    """Every advisory id the lockfile triggers, nothing ignored: the audit runs from a scratch
    directory, where no `.cargo/audit.toml` is read."""
    lockfile = os.path.abspath(lockfile)
    with (
        tempfile.TemporaryDirectory(dir=goh_tmp(), prefix="goh-audit.") as scratch,
        advisory_db.held(fetch) as db,
    ):
        r = advisory_db.bounded(  # the audit's own ceilings (GOH.AUDITSPIN)
            ["cargo", "audit", "--json", *advisory_db.audit_args(db, fetch), "--file", lockfile],
            fetch,
            cwd=scratch,
            env=foreign_repo_env(),
        )
    try:
        report = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"cargo audit gave no report ({r.returncode}): {r.stderr.strip()[-300:]}"
        ) from e
    ids = {v["advisory"]["id"] for v in report.get("vulnerabilities", {}).get("list", [])}
    for warnings in report.get("warnings", {}).values():
        ids |= {w["advisory"]["id"] for w in warnings if w.get("advisory")}
    return ids


def lockfile() -> str | None:
    """The workspace's Cargo.lock, or None: the subject this check judges against, so a tree
    without one is a refusal, never a pass over nothing (check_empty_scope.py)."""
    r = subprocess.run(
        ["cargo", "locate-project", "--workspace", "--message-format", "plain"],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    lock = os.path.join(os.path.dirname(r.stdout.strip()), "Cargo.lock")
    return lock if r.returncode == 0 and os.path.isfile(lock) else None


def estate_locks(root: Path, registry: bool) -> list[str]:
    """Every tracked Cargo.lock of every repo in the estate."""
    walked = estate_registry.walk_repos(root)
    repos = estate_registry.union(walked)[0] if registry else walked
    locks = []
    for repo in repos:
        r = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z", "--", "Cargo.lock", "**/Cargo.lock"],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            env=foreign_repo_env(),
        )
        locks += [str(repo / p) for p in r.stdout.split("\0") if p]
    return locks


def shared(root: Path, registry: bool, fetch: bool) -> int:
    """The estate ratchet over goh's shared rulings."""
    try:
        entries = shared_advisories.load()
    except (OSError, ValueError) as e:
        err(f"[audit_ignores] {e}")
        return 1
    if not entries:
        ok("[audit_ignores] no shared advisory ruling")
        return 0
    locks = estate_locks(root, registry)
    try:
        live = firing_any(locks, fetch=fetch)
    except RuntimeError as e:
        err(f"[audit_ignores] could not read which advisories fire: {e}")
        return 1
    src = shared_advisories.path()
    bad = [
        f"{src}: {e.id} fires in no estate lock ({len(locks)} under {root}) -- delete the entry"
        for e in entries
        if e.id not in live
    ]
    for lock in locks:
        text = Path(lock).read_text(encoding="utf-8")
        bad += [
            f"{src}: {e.id}: {e.blocker} no longer depends on {e.crate} in {lock} ({e.reopen})"
            for e in entries
            if shared_advisories.dropped(e, text)
        ]
    for line in bad:
        err(f"[audit_ignores] {line}")
    if bad:
        return 1
    ok(f"[audit_ignores] {counted(len(entries), 'shared ruling')}, each still firing in the estate")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--no-fetch", action="store_true", help="judge against the cached database")
    ap.add_argument("--repo", help="judge against every lock rust_locks audits from this dir")
    ap.add_argument("--lock", help="with --repo: the gate's own workspace lock, tracked or not")
    ap.add_argument("--shared", action="store_true", help="judge goh's shared rulings")
    ap.add_argument("--estate", help="with --shared: the estate root (no registry)")
    args = ap.parse_args()
    if args.shared:
        default = os.environ.get("GOH_ESTATE_ROOT") or str(Path.home() / "Projects")
        return shared(Path(args.estate or default), not args.estate, not args.no_fetch)
    if args.repo:
        repo = Path(args.repo).resolve()
        cfg = Path.cwd().resolve().relative_to(repo).as_posix() or "."
        locks = [str(repo / lk) for lk in rust_locks.governed(repo, cfg, args.lock)]
        what = (
            ", ".join(os.path.relpath(lk) for lk in locks)
            if len(locks) <= 3
            else f"any of the {len(locks)} locks it governs"
        )
    else:
        lock = lockfile()
        locks = [lock] if lock else []
        what = "Cargo.lock"
    if not locks:
        err(
            f"[audit_ignores] no Cargo workspace with a Cargo.lock at {os.getcwd()}: nothing to judge"
        )
        return 1
    if not os.path.exists(CONFIG):
        ok("[audit_ignores] no .cargo/audit.toml: nothing is exempted")
        return 0
    with open(CONFIG, encoding="utf-8") as f:
        entries = ignore_lines(f.read())
    if not entries:
        ok("[audit_ignores] the ignore list is empty")
        return 0
    bad = [
        f"{CONFIG}:{n}: {aid} has no `# reason` on its line" for n, aid, why in entries if not why
    ]
    try:
        live = firing_any(locks, fetch=not args.no_fetch)
    except RuntimeError as e:
        err(f"[audit_ignores] could not read which advisories fire: {e}")
        return 1
    bad += [
        f"{CONFIG}:{n}: {aid} no longer fires against {what} -- delete the entry"
        for n, aid, _ in entries
        if aid not in live
    ]
    for line in bad:
        err(f"[audit_ignores] {line}")
    if bad:
        return 1
    ok(
        f"[audit_ignores] {counted(len(entries), 'exemption')}, each with its reason, each "
        f"still firing"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
