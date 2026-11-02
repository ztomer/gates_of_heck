#!/usr/bin/env python3
"""lib/kept_rewrite.py -- the verdict on a green step that REWROTE a path GOH_EXPORT_KEEP keeps.

THE CLASS (GOH.KEEP1; divoom-control P1.14/P1.17, 2026-11-01). The proven-step cache keys every
step on the CONTENT of the ignored paths `GOH_EXPORT_KEEP` names (gates/_proven.sh). A step that
writes into one of them moves its own key while it runs, so it is never recorded as proven and
re-runs on every gate, every time -- and the only trace was an info line. On divoom six pre-push
steps rebuilt `target/debug/divoomd`, a kept file, and nothing said so for weeks.

THE RATCHET. Each runner (gates/proven.sh, gates/local_ci.sh, gates/_rust_proven.sh) asks this
after a green step, with the kept entries whose content moved (`--rewrote`, one per line):
  - a rewrite the repo's `kept_rewrite_allow.json` names is a WARNING naming repo, step and path;
  - a rewrite it does not name is RED: a new instance of the class;
  - an entry whose step ran green WITHOUT rewriting its path is RED: the excuse is stale, delete it;
  - an entry naming a path GOH_EXPORT_KEEP no longer keeps, or with no reason, is RED.
The allowlist sits at the repo's top level, tracked, so the push gate's export carries it:

    {"entries": [{"step": "<step string, or rust_gate's group label>", "path": "<kept entry>",
                  "reason": "why it may stand, and what would retire it"}]}

Output: one `warn <text>` or `err <text>` line per finding, for the caller's tui. Exit 0 green, 1
red, 2 when the allowlist cannot be read.
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

import argparse
import json
import sys
from pathlib import Path

ALLOW_FILE = "kept_rewrite_allow.json"
REMEDY = (
    "a step that writes a kept path moves its own proven key: make it write elsewhere (its own"
    " environment, an out-of-tree build dir), or stop keeping a build output (divoom P1.17); if"
    f" neither can be done yet, name it in {ALLOW_FILE} with the reason"
)


def _norm(path: str) -> str:
    return path.strip().rstrip("/")


def load(path: Path) -> list[dict[str, str]]:
    """The entries, or [] when the repo has no allowlist. Raises TypeError/ValueError on a malformed file."""
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise TypeError(f"{ALLOW_FILE}: the top level must be an object with an 'entries' list")
    out = []
    for i, e in enumerate(entries):
        ok = isinstance(e, dict) and all(
            isinstance(e.get(k), str) and e[k].strip() for k in ("step", "path", "reason")
        )
        if not ok:
            raise TypeError(f"{ALLOW_FILE}: entry {i} needs a non-empty step, path and reason")
        out.append({"step": e["step"], "path": _norm(e["path"]), "reason": e["reason"].strip()})
    return out


def verdict(
    entries: list[dict[str, str]], repo: str, step: str, keep: str, rewrote: list[str]
) -> tuple[list[tuple[str, str]], bool]:
    """(lines, red) for one green step. Pure: every input is an argument."""
    kept = {_norm(k) for k in keep.split() if _norm(k)}
    moved = sorted({_norm(p) for p in rewrote if _norm(p)})
    lines: list[tuple[str, str]] = []

    def say(kind: str, text: str) -> None:
        lines.append((kind, text))

    for e in entries:
        if e["path"] not in kept:
            say("err", f"{repo}: {ALLOW_FILE} names '{e['path']}' for step '{e['step']}', and"
                " GOH_EXPORT_KEEP does not keep it -- a stale entry, delete it")  # fmt: skip
    mine = {e["path"]: e for e in entries if e["step"] == step}
    for p in moved:
        head = f"{repo}: step '{step}' rewrote kept path '{p}' -- never recorded as proven"
        if p in mine:
            say("warn", f"{head} (on the {ALLOW_FILE} ratchet: {mine[p]['reason']})")
        else:
            say("err", f"{head}, and {ALLOW_FILE} does not name it")
            say("err", f"  {REMEDY}")
    for p in sorted(set(mine) - set(moved)):
        if p in kept:
            say("err", f"{repo}: {ALLOW_FILE} says step '{step}' rewrites '{p}', and it ran green"
                " without touching it -- a stale entry, delete it")  # fmt: skip
    red = any(kind == "err" for kind, _ in lines)
    return lines, red


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--allow", required=True, help="the repo's allowlist (absent = empty)")
    ap.add_argument("--repo", required=True, help="the repo's name, for the message")
    ap.add_argument("--step", required=True, help="the step string (or rust_gate's group label)")
    ap.add_argument("--keep", default="", help="GOH_EXPORT_KEEP, verbatim")
    ap.add_argument("--rewrote", default="", help="kept entries whose content moved, one per line")
    a = ap.parse_args(argv)
    try:
        entries = load(Path(a.allow))
    except (ValueError, TypeError, OSError) as exc:
        print(f"err {a.repo}: {exc}")
        return 2
    lines, red = verdict(entries, a.repo, a.step, a.keep, a.rewrote.splitlines())
    for kind, text in lines:
        print(f"{kind} {text}")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
