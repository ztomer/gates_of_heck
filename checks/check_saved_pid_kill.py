#!/usr/bin/env python3
"""No signal to a SAVED pid without proof it is still the process it was saved for.

GOH.STRAYSIGNAL.

    checks/check_saved_pid_kill.py [ROOT]          # exit 0, or 1 naming each bare kill
    checks/check_saved_pid_kill.py --list [ROOT]   # every signal site, classified

THE INCIDENT (2026-11-20). In a loaded full-suite land a fake build's `sleep 60` ended after about a
second. A pid is reused once its process is gone, and the suite allocates hundreds a second, so a
`finally: os.kill(int(pidfile.read_text()), SIGKILL)` run after its process had already stopped
signals whatever now holds the number. lib/pid_owner.py (and .sh) is the one route for a saved pid:
it stamps the process's birth and refuses a pid whose birth changed.

WHAT IS JUDGED. Every signal site in the tracked code of tests/ lib/ tools/ checks/ hooks/ crates/
gates/ scripts/:
  * Python (`ast`): a call of `os.kill`/`os.killpg` (or `kill`/`killpg` imported from os) is OWNED
    when its target is a child handle (`X.pid`, a Popen's; `os.getpgid(X.pid)`), this process or
    its group (`os.getpid()`, `os.getpgrp()`, `os.getpgid(0)`, `0`); any other target is BARE.
  * Shell (`*.sh`, `*.bash`, a `#!` sh/bash file): a `kill` command whose targets are not all `$$`,
    `$BASHPID` or a `%job`, and that is not a listing (`-l`), is BARE.
  * Rust: a `libc::kill(`/`libc::killpg(`/`goh_sys::killpg(`/`nix ... signal::kill(` call is BARE
    (Rust's own `Child::kill()` is a handle).
  A PROBE (signal 0: `os.kill(pid, 0)`, `kill -0 "$pid"`) is judged as a kill is (GOH.STRAYSIGNAL2):
  it signals nothing, but it answers yes for whatever process holds a reused number, so a lock read
  a dead holder's pid as its holder still running, and a test waiting for a process to end waited
  on a stranger. A saved pid's liveness goes through `pid_owner.alive`/`alive_line` or
  `goh_pid_alive`, which compare the start time.
  The helpers themselves (lib/pid_owner.py, lib/pid_owner.sh) are not judged.

THE RATCHET: checks/saved_pid_kill_allow.json, one entry per (path, call) -- the call's text with
its
whitespace collapsed -- with a status and a reason. `legitimate`: owned in a way the scanner cannot
see (the group of a child it spawned, passed in by name), the reason saying how. `bare`: today's
debt, a saved pid signalled without proof; it may only shrink. A bare site not listed is red, and
so is an entry no site matches (stale), an entry without a reason, and a status outside the two.

NOT READ (stated limits, GOH.STRAYSIGNAL2):
  * shell inside a Python string -- a fake or a hook a test writes (`f"kill {pid}"`,
    `f"kill -0 {pid}"`): it is data to the Python parser, and its pid is one the TEST formats in,
    so such a string is judged by review, not here;
  * a `kill` run through `subprocess` (`["kill", pid]`), and a pid passed through more than one
    name;
  * WHEN the stamp was taken. `pid_owner.signal(pid_owner.stamp(pid), sig)` passes, and proves
    nothing: a stamp taken at the moment of the kill stamps whoever holds the number then. A stamp
    is proof only when taken while the process is known to be ours (at the spawn, or as its
    pidfile first appears); that is a property of the data flow, which a per-call scan cannot see.
Calibrated by tests/test_check_saved_pid_kill.py, which plants each shape and watches it go red.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: run as is
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT)]
from tui.lib import counted, err, ok, warn  # noqa: E402 -- the root is on the path from the line above

TREE_READS = ("tests/", "lib/", "tools/", "checks/", "hooks/", "crates/", "gates/", "scripts/")
ALLOW = "checks/saved_pid_kill_allow.json"
HELPERS = frozenset({"lib/pid_owner.py", "lib/pid_owner.sh"})
STATUSES = ("legitimate", "bare")
SELF_PY = frozenset({"getpid", "getpgrp"})
SHELL_EXT = (".sh", ".bash")
SHEBANG = re.compile(r"^#!.*\b(?:ba|z|da)?sh\b")
# A `kill` COMMAND: at a command position (line start, after ; & | ( { ` $( or a keyword, or the
# opening quote of a trap/sh -c string), never a word inside another (`pkill`, `kill_tree`).
SH_KILL = re.compile(
    r"""(?:^|[;&|({`]|\$\(|\b(?:then|do|else|elif|if|while|until|time|exec|!)\s|['"])\s*"""
    r"""(?:command\s+|builtin\s+|/bin/)?kill(?=[ \t])([^;&|)`\n]*)"""
)
SH_SELF = re.compile(r"""^["']?(?:\$\$|\$\{?BASHPID\}?|%\S*|-\$\$)["']?$""")
RS_KILL = re.compile(r"\b(?:libc|goh_sys|signal|super)::kill(?:pg)?\s*\(")


@dataclass(frozen=True)
class Site:
    path: str
    line: int
    call: str  # whitespace-collapsed text: the allowlist key
    owned: str  # why it is owned, or "" when bare

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.call}"


def squash(text: str) -> str:
    return " ".join(text.split())


def _is_os(node: ast.AST, names: frozenset[str] | set[str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
        and node.func.attr in names
    )


def _py_target_owned(node: ast.AST) -> str:
    """Why a Python kill target is provably ours, or ""."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return _py_target_owned(node.operand)
    if isinstance(node, ast.Constant) and node.value == 0:
        return "this process's own group"
    if isinstance(node, ast.Attribute) and node.attr == "pid":
        return "a child handle's pid"
    if _is_os(node, SELF_PY):
        return "this process"
    if _is_os(node, {"getpgid"}) and isinstance(node, ast.Call) and node.args:
        return _py_target_owned(node.args[0]) and "the group of a process it owns"
    return ""


def python_sites(path: str, text: str) -> list[Site]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    from_os = {
        a.asname or a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module == "os"
        for a in n.names
        if a.name in {"kill", "killpg"}
    }
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        f = node.func
        hit = (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id == "os"
            and f.attr in {"kill", "killpg"}
        ) or (isinstance(f, ast.Name) and f.id in from_os)
        if not hit:
            continue
        # A probe (signal 0) is judged as a kill is (GOH.STRAYSIGNAL2): it answers yes for
        # whatever holds a reused number, so a saved pid's liveness goes through pid_owner.alive.
        owned = _py_target_owned(node.args[0])
        out.append(Site(path, node.lineno, squash(ast.unparse(node)), owned))
    return out


def _sh_code(line: str) -> str:
    s = line.lstrip()
    if s.startswith("#"):
        return ""
    return re.split(r"\s#", line, maxsplit=1)[0]


def _sh_owned(args: str) -> str:
    words = args.split()
    targets, i = [], 0
    while i < len(words):
        w = words[i].strip("\"'")
        if w in {"-l", "-L"}:
            return "a listing, which signals nothing"
        if w == "-s" or w == "-n":
            i += 2
            continue
        if w == "--":
            i += 1
            continue
        if re.fullmatch(r"-[A-Za-z0-9]+", w):
            i += 1
            continue
        targets.append(words[i])
        i += 1
    if not targets:
        return "no target"
    if all(SH_SELF.match(t) for t in targets):
        return "this shell or a job of its own"
    return ""


def shell_sites(path: str, text: str) -> list[Site]:
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        code = _sh_code(line)
        for m in SH_KILL.finditer(code):
            call = squash("kill" + m.group(1))
            out.append(Site(path, n, call, _sh_owned(m.group(1))))
    return out


def rust_sites(path: str, text: str) -> list[Site]:
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        code = line.split("//", 1)[0]
        for m in RS_KILL.finditer(code):
            if re.search(r"\bfn\s*$", code[: m.start()]):
                continue
            rest = code[m.start() :]
            depth, end = 0, len(rest)
            for i, c in enumerate(rest):
                depth += c == "("
                depth -= c == ")"
                if c == ")" and depth == 0:
                    end = i + 1
                    break
            out.append(Site(path, n, squash(rest[:end]), ""))
    return out


def _tracked(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True,
                         text=True, check=True, stdin=subprocess.DEVNULL).stdout  # fmt: skip
    return [p for p in out.split("\0") if p]


def sites_of(path: str, text: str) -> list[Site]:
    if path in HELPERS:
        return []
    if path.endswith(".py"):
        return python_sites(path, text)
    if path.endswith(".rs"):
        return rust_sites(path, text)
    if path.endswith(SHELL_EXT) or ("." not in Path(path).name and SHEBANG.match(text)):
        return shell_sites(path, text)
    return []


def scan(
    root: Path, plant: dict[str, str] | None = None, tracked: bool = True
) -> tuple[list[Site], int]:
    """Every signal site in the judged tree, and how many files were read. `plant` adds (or
    replaces) files as if tracked: the calibration seam; `tracked=False` reads the plant alone."""
    files = {p: None for p in (_tracked(root) if tracked else []) if p.startswith(TREE_READS)}
    files.update(dict.fromkeys(plant or {}))
    found, read = [], 0
    for rel in sorted(files):
        if plant and rel in plant:
            text = plant[rel]
        else:
            try:
                text = (root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        read += 1
        found.extend(sites_of(rel, text))
    return found, read


def load_allow(
    root: Path, allow: dict | None = None
) -> tuple[dict[tuple[str, str], str], list[str]]:
    """{(path, call): status} and the problems with the file itself."""
    if allow is None:
        try:
            allow = json.loads((root / ALLOW).read_text(encoding="utf-8"))
        except FileNotFoundError:
            allow = {"entries": []}
        except (OSError, json.JSONDecodeError) as e:
            return {}, [f"{ALLOW} is not readable JSON: {e}"]
    entries, problems = {}, []
    for i, e in enumerate(allow.get("entries", [])):
        path, call, status, reason = (
            str(e.get(k, "")).strip() for k in ("path", "call", "status", "reason")
        )
        if not (path and call and reason):
            problems.append(f"{ALLOW} entry {i}: needs a path, a call and a reason")
        elif status not in STATUSES:
            problems.append(
                f"{ALLOW} entry {i} ({path}): status must be one of {', '.join(STATUSES)}"
            )
        elif (path, squash(call)) in entries:
            problems.append(f"{ALLOW} entry {i} ({path}): listed twice: {call}")
        else:
            entries[(path, squash(call))] = status
    return entries, problems


def check(
    root: Path, plant: dict[str, str] | None = None, allow: dict | None = None, tracked: bool = True
):
    """(findings, the sites, files read, the allowlisted bare count)."""
    sites, read = scan(root, plant, tracked)
    entries, findings = load_allow(root, allow)
    if read == 0:
        findings.append(
            "no file of the judged tree was read: refusing to report clean over nothing"
        )
    keys = {(s.path, s.call) for s in sites if not s.owned}
    for s in sites:
        if not s.owned and (s.path, s.call) not in entries:
            findings.append(
                f"{s}: a signal to a saved pid with no proof it is still the process it was saved "
                "for -- stamp it while it is yours and signal through lib/pid_owner.py "
                "(`stamp`/`signal`, `alive` for a probe) or lib/pid_owner.sh "
                "(`goh_pid_stamp`/`goh_pid_kill`, `goh_pid_alive`)"
            )
    for key, status in entries.items():
        if key not in keys:
            findings.append(
                f"{ALLOW}: stale {status} entry, no bare site matches it: {key[0]}: {key[1]}"
            )
    debt = sum(1 for k, st in entries.items() if st == "bare" and k in keys)
    return findings, sites, read, debt


def main(argv: list[str]) -> int:
    listing = "--list" in argv
    rest = [a for a in argv if a != "--list"]
    root = Path(rest[0]).resolve() if rest else ROOT
    findings, sites, read, debt = check(root)
    if listing:
        for s in sites:
            print(f"{'owned' if s.owned else 'BARE '}  {s}" + (f"  ({s.owned})" if s.owned else ""))
    if findings:
        for f in findings:
            err(f"[saved_pid_kill] {f}")
        return 1
    owned = sum(1 for s in sites if s.owned)
    ok(
        f"[saved_pid_kill] OK -- {counted(len(sites), 'signal site')} in {counted(read, 'file')}: "
        f"{owned} owned, {len(sites) - owned} listed in {ALLOW}"
    )
    if debt:
        warn(
            f"[saved_pid_kill]   {debt} of them 'bare': saved pids signalled with no proof, to move"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
