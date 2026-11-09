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

    scripts/apply_claude_hooks.py [--settings PATH] [--agents-dir DIR] [--dry-run | --check]

Writes ~/.claude/settings.json by default, after a `settings.json.goh-bak-<time>` copy, through a
temp file and `os.replace`, and only when something would change. `--dry-run` prints the diff;
`--check` prints it and exits 1 when settings.json is not as registrations.json says (the weekly
`model_choice_watch.py` reads the same drift). Pinned by tests/test_apply_claude_hooks.py.

GOH.LEANAGENT (2026-11-08): it also installs goh's lean subagent types, `hooks/claude/agents/*.md`,
into `~/.claude/agents/` (`--agents-dir`), byte for byte; `--check` counts a missing or differing
one as drift. Only a file named like one of goh's is written; any other agent file is left alone.
GOH.AGENTRETIRE (2026-11-08): what it wrote is recorded in `<agents-dir>/.goh-installed.json` (name ->
sha256), and a type goh no longer ships is removed -- only when the manifest names it and the file
still holds goh's bytes; an edited one is left (warned), a foreign one is never read.

GOH.READCAP / GOH.CACHETTL (2026-11-08): `registrations.json`'s `settings` holds the plain Claude
Code settings goh manages (`env` vars merged one by one, top-level keys set); every other key and
env var is the owner's and is never touched. Why each value is what it is, measured:
docs/config.md "Managed Claude Code settings".
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import difflib
import hashlib
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
from tui.lib import err, info, ok, warn

REGISTRATIONS = ROOT / "hooks" / "claude" / "registrations.json"
DEFAULT_SETTINGS = Path.home() / ".claude" / "settings.json"
AGENT_SOURCES = ROOT / "hooks" / "claude" / "agents"
DEFAULT_AGENTS = Path.home() / ".claude" / "agents"
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
    _settle(out, regs.get("settings") or {}, notes)
    return out, notes


def managed_settings(regs: dict[str, Any]) -> dict[str, Any]:
    """`registrations.json`'s plain settings as dotted keys (`env.X`, `subagentPromptCacheTtl`)."""
    flat: dict[str, Any] = {}
    for key, value in (regs.get("settings") or {}).items():
        if key.startswith("_"):
            continue
        if key == "env":
            flat.update({f"env.{k}": v for k, v in value.items()})
        else:
            flat[key] = value
    return flat


def _settle(out: dict[str, Any], managed: dict[str, Any], notes: list[str]) -> None:
    """Put each managed plain setting in place; `env` is merged var by var, never replaced."""
    for dotted, value in managed_settings({"settings": managed}).items():
        parent, key = (
            (out.setdefault("env", {}), dotted[4:]) if dotted.startswith("env.") else (out, dotted)
        )
        if parent.get(key) != value:
            parent[key] = value
            notes.append(f"set {dotted}")


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


def agent_drift(sources: Path, dest: Path) -> list[Path]:
    """goh's agent definitions whose installed copy under `dest` is missing or differs."""
    out = []
    for src in sorted(sources.glob("*.md")):
        dst = dest / src.name
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            out.append(src)
    return out


MANIFEST = ".goh-installed.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(dest: Path) -> dict[str, str]:
    """What goh installed into `dest`: file name -> sha256 of the bytes it wrote (GOH.AGENTRETIRE)."""
    try:
        data = json.loads((dest / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    got = data.get("installed") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in got.items()} if isinstance(got, dict) else {}


def retired_agents(sources: Path, dest: Path) -> tuple[list[str], list[str]]:
    """(removable, kept): agent files goh installed and no longer ships. Removable when the file
    still holds the bytes goh wrote; kept (the owner's now) when it was edited since."""
    shipped = {p.name for p in sources.glob("*.md")}
    removable, kept = [], []
    for name, sha in sorted(_manifest(dest).items()):
        path = dest / name
        if name in shipped or not path.is_file():
            continue
        (removable if _sha(path) == sha else kept).append(name)
    return removable, kept


def apply_agents(sources: Path, dest: Path, check: bool, dry_run: bool) -> int:
    """Install goh's agent types and remove the ones it retired; 0 when in place (or written), 1 on
    drift in --check. Only files the manifest says goh wrote are ever removed."""
    drift = agent_drift(sources, dest)
    removable, kept = retired_agents(sources, dest)
    for name in kept:
        warn(
            f"agent {Path(name).stem}: retired by goh but edited since -- left in place, now yours"
        )
    for src in drift:
        info(f"agent {src.stem}: {'differs' if (dest / src.name).exists() else 'missing'}")
    for name in removable:
        info(
            f"agent {Path(name).stem}: retired by goh -- {'to remove' if check or dry_run else 'removed'}"
        )
    if check:
        return 1 if drift or removable else 0
    if dry_run:
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    for src in drift:
        _write(dest / src.name, src.read_text(encoding="utf-8"))
    for name in removable:
        (dest / name).unlink()
    installed = {p.name: _sha(p) for p in sorted(sources.glob("*.md"))}
    if _manifest(dest) != installed:
        _write(dest / MANIFEST, json.dumps({"installed": installed}, indent=2) + "\n")
    if agent_drift(sources, dest) or retired_agents(sources, dest)[0]:
        err(f"{dest} still differs after the write")
        return 1
    if drift or removable:
        ok(f"{dest}: {len(drift)} agent type(s) written, {len(removable)} retired one(s) removed")
    else:
        ok(f"{dest}: goh's agent types are installed")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    ap.add_argument("--registrations", type=Path, default=REGISTRATIONS)
    ap.add_argument("--agents-dir", type=Path, default=DEFAULT_AGENTS)
    ap.add_argument("--agent-sources", type=Path, default=AGENT_SOURCES, help=argparse.SUPPRESS)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    mode.add_argument(
        "--check", action="store_true", help="print the diff; exit 1 if it is not empty"
    )
    args = ap.parse_args(argv)
    hooks_rc = apply_hooks(args)
    agents_rc = apply_agents(args.agent_sources, args.agents_dir, args.check, args.dry_run)
    return hooks_rc or agents_rc


def apply_hooks(args: argparse.Namespace) -> int:
    path: Path = args.settings
    regs = json.loads(args.registrations.read_text(encoding="utf-8"))
    raw = path.read_text(encoding="utf-8") if path.exists() else "{}\n"
    try:
        settings = json.loads(raw)
        if not isinstance(settings, dict) or not isinstance(settings.get("hooks", {}), dict):
            raise TypeError("top level and `hooks` must be objects")
        if not isinstance(settings.get("env", {}), dict):
            raise TypeError("`env` must be an object")
        for ev, groups in (settings.get("hooks") or {}).items():
            if not isinstance(groups, list):
                raise TypeError(f"hooks.{ev} must be a list")
    except (ValueError, TypeError) as e:
        err(f"{path} is not settings goh can edit ({e}); left as it was")
        return 2
    new, notes = apply(settings, regs)
    if not notes:
        ok(f"{path}: goh's hooks and managed settings are as registered")
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
