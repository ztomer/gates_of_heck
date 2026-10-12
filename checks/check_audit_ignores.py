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

    python3 checks/check_audit_ignores.py [--no-fetch]   # from the workspace root

rust_gate.sh passes --no-fetch: its own audit step fetched the database a moment before.
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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import advisory_db

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import foreign_repo_env

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, ok

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


def firing(lockfile: str, fetch: bool) -> set[str]:
    """Every advisory id the lockfile triggers, nothing ignored: the audit runs from a scratch
    directory, where no `.cargo/audit.toml` is read."""
    with tempfile.TemporaryDirectory(prefix="goh-audit.") as scratch, advisory_db.held(fetch) as db:
        r = subprocess.run(
            ["cargo", "audit", "--json", *advisory_db.audit_args(db, fetch), "--file", lockfile],
            check=False,
            cwd=scratch,
            env=foreign_repo_env(),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--no-fetch", action="store_true", help="judge against the cached database")
    args = ap.parse_args()
    lock = lockfile()
    if lock is None:
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
        live = firing(lock, fetch=not args.no_fetch)
    except RuntimeError as e:
        err(f"[audit_ignores] could not read which advisories fire: {e}")
        return 1
    bad += [
        f"{CONFIG}:{n}: {aid} no longer fires against Cargo.lock -- delete the entry"
        for n, aid, _ in entries
        if aid not in live
    ]
    for line in bad:
        err(f"[audit_ignores] {line}")
    if bad:
        return 1
    ok(f"[audit_ignores] {len(entries)} exemption(s), each with its reason, each still firing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
