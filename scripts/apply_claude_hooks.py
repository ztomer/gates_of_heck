#!/usr/bin/env python3
"""Write goh's Claude Code hook entries (hooks/claude/registrations.json) into settings.json.

GOH.SHAREDGRP (2026-11-02). A settings.json hook group is `{matcher, hooks: [...]}`: the matcher
belongs to the GROUP. So widening the matcher for a goh hook widens it for every hook in the group,
and GOH.MONGUARD's hand edit made `rtk hook claude` start rewriting Monitor commands (its
`updatedInput` carries only `command`, so Monitor's other fields can be dropped).

The rule this applies: goh creates or widens only a group whose hooks are ALL goh's, and all of
them registered with that matcher. A goh hook that shares a group with a foreign hook (or with a
goh hook that wants another matcher) is SPLIT out into its own group, placed right after the old
one, and the old group keeps its matcher byte for byte. A shared group whose matcher already fits is
left alone. Foreign groups and hooks are never edited. Idempotent: a second run changes nothing.

    scripts/apply_claude_hooks.py [--settings PATH] [--dry-run | --check]

Writes ~/.claude/settings.json by default, after a `settings.json.goh-bak-<time>` copy, through a
temp file and `os.replace`, and only when something would change. `--dry-run` prints the diff;
`--check` prints it and exits 1 when settings.json is not as registrations.json says (the weekly
`model_choice_watch.py` reads the same drift). Pinned by tests/test_apply_claude_hooks.py.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import difflib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tui.lib import err, info, ok

REGISTRATIONS = ROOT / "hooks" / "claude" / "registrations.json"
DEFAULT_SETTINGS = Path.home() / ".claude" / "settings.json"
SCRIPT = re.compile(r"hooks/claude/([\w.]+)")


def is_goh(command: object) -> bool:
    """A hook goh owns: it runs a script under hooks/claude/ of the goh checkout."""
    cmd = str(command or "")
    return "hooks/claude/" in cmd and ("GOH_DIR" in cmd or "gates_of_heck" in cmd)


def _script(command: object) -> str | None:
    m = SCRIPT.search(str(command or ""))
    return m.group(1) if m and is_goh(command) else None


def _matcher(group: dict[str, Any]) -> str | None:
    return group.get("matcher") or None


def _group(matcher: str | None, hook: dict[str, Any]) -> dict[str, Any]:
    return {**({"matcher": matcher} if matcher else {}), "hooks": [hook]}


def _fit(hook: dict[str, Any], entry: dict[str, Any]) -> bool:
    """Make `hook` say what the registration says; True when that changed it."""
    before = copy.deepcopy(hook)
    hook.setdefault("type", "command")
    hook["command"] = entry["command"]
    if entry.get("timeout") is not None:
        hook["timeout"] = entry["timeout"]
    return hook != before


def _new_hook(entry: dict[str, Any]) -> dict[str, Any]:
    hook: dict[str, Any] = {}
    _fit(hook, entry)
    return hook


def apply(settings: dict[str, Any], regs: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The settings with goh's registrations in place, and a note per change. Never mutates."""
    out = copy.deepcopy(settings)
    notes: list[str] = []
    for event, entries in regs["hooks"].items():
        wanted = {_script(e["command"]): e for e in entries}
        groups = out.setdefault("hooks", {}).setdefault(event, [])
        for entry in entries:
            _place(groups, event, entry, wanted, notes)
    return out, notes


def _find(groups: list[dict[str, Any]], script: str | None) -> tuple[int, int] | None:
    for gi, g in enumerate(groups):
        for hi, h in enumerate(g.get("hooks") or []):
            if _script(h.get("command")) == script:
                return gi, hi
    return None


def _place(groups: list[dict[str, Any]], event: str, entry: dict[str, Any],
           wanted: dict[str | None, dict[str, Any]], notes: list[str]) -> None:  # fmt: skip
    script, want = _script(entry["command"]), entry.get("matcher") or None
    label = f"{event} {script} [{want or '*'}]"
    at = _find(groups, script)
    if at is None:
        groups.append(_group(want, _new_hook(entry)))
        notes.append(f"add {label}")
        return
    gi, hi = at
    group = groups[gi]
    hook = group["hooks"][hi]
    peers = [h for i, h in enumerate(group["hooks"]) if i != hi]
    # exclusive: every peer is a goh hook registered with the same matcher, so widening is safe
    exclusive = all(is_goh(p.get("command")) and (wanted.get(_script(p.get("command"))) or {}).get("matcher") == entry.get("matcher") for p in peers)  # fmt: skip
    if exclusive:
        changed = _fit(hook, entry)
        if _matcher(group) != want:
            groups[gi] = {**({"matcher": want} if want else {}),
                          **{k: v for k, v in group.items() if k != "matcher"}}  # fmt: skip
            changed = True
        if changed:
            notes.append(f"fit {label}")
    elif _matcher(group) == want:  # shared, and the matcher already fits: nothing to widen
        if _fit(hook, entry):
            notes.append(f"fit {label} (shared group, matcher untouched)")
    else:  # shared, and the matcher does not fit: split first, so no foreign hook is widened
        del group["hooks"][hi]
        groups.insert(gi + 1, _group(want, _new_hook(entry)))
        notes.append(f"split {label} out of a group with {len(group['hooks'])} other hook(s)")


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2) + "\n"


def _write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    if path.exists():
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    ap.add_argument("--registrations", type=Path, default=REGISTRATIONS)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    mode.add_argument(
        "--check", action="store_true", help="print the diff; exit 1 if it is not empty"
    )
    args = ap.parse_args(argv)
    path: Path = args.settings
    regs = json.loads(args.registrations.read_text(encoding="utf-8"))
    raw = path.read_text(encoding="utf-8") if path.exists() else "{}\n"
    try:
        settings = json.loads(raw)
        if not isinstance(settings, dict) or not isinstance(settings.get("hooks", {}), dict):
            raise TypeError("top level and `hooks` must be objects")
        for ev, groups in (settings.get("hooks") or {}).items():
            if not isinstance(groups, list):
                raise TypeError(f"hooks.{ev} must be a list")
    except (ValueError, TypeError) as e:
        err(f"{path} is not settings goh can edit ({e}); left as it was")
        return 2
    new, notes = apply(settings, regs)
    if not notes:
        ok(f"{path}: goh's hooks are as registered")
        return 0
    diff = "".join(difflib.unified_diff(raw.splitlines(True), _dump(new).splitlines(True),
                                        str(path), f"{path} (applied)"))  # fmt: skip
    for n in notes:
        info(n)
    sys.stdout.write(diff)
    if args.check:
        return 1
    if args.dry_run:
        return 0
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.goh-bak-{dt.datetime.now():%Y%m%dT%H%M%S}"))
    _write(path, _dump(new))
    if apply(json.loads(path.read_text(encoding="utf-8")), regs)[1]:
        err(f"{path} still differs after the write")
        return 1
    ok(f"{path}: {len(notes)} change(s) written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
