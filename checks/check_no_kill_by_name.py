#!/usr/bin/env python3
"""Fail if any git-tracked code or script kills processes BY NAME.

    pkill -9 -f "camoufox -no-remote"          # every camoufox on the machine
    killall Firefox                            # every Firefox, whoever owns it
    kill $(pgrep -f worker)                    # the same thing in two steps
    subprocess.run(["pkill", "-f", pattern])   # the same thing from Python

A name is not an owner. On 2026-09-23 at 21:58 zinc's T6 harness ended every
capture with `pkill -9 -f "Camoufox.app"`, meaning "the browsers this run left
behind". It also matched the Gemini driver another session had open on a
different profile, and SIGKILLed it in the middle of a reply. That was the
third failure in a row, and it stopped the necrohand campaign. Nothing in the
system log recorded the kill. The only trace was two unrelated browser trees
dying in the same second.

The rule: kill what you started, by the pid or the process group you hold, or
by walking down from your own pid. A pkill scoped by `-P <ppid>`, `-g <pgrp>` or
`-s <sid>` (`--parent`, `--pgroup`, `--session`) is that, and passes. A
`killall` never is. The same goes for a `pgrep` or `pidof` whose output feeds a
`kill` on the same line. A multi-line lookup (pgrep on one line, `os.kill` on a
later one) is outside what a line scan can see; that is a known limit, not a
pass.

    python3 checks/check_no_kill_by_name.py              # all tracked files (CI gate)
    python3 checks/check_no_kill_by_name.py --staged     # the index (pre-commit)
    python3 checks/check_no_kill_by_name.py --exclude '^vendor/'

SCOPE. Code and scripts: shell, Python, Rust, Swift, C family, JS/TS, Go, Ruby,
Makefiles and justfiles, CI YAML, and extensionless files with a shebang. Prose
(Markdown, text) is out of scope: a document that explains this rule has to be
able to name the command. Comments are stripped. For Python, ast and tokenize
drop comments and docstrings exactly. For other languages, whole-line comments
and a trailing ` #` or ` //` comment are dropped.

EXEMPTIONS live in `kill_by_name_allow.json` at the repo root. It is a
ratchet, not a rug:

    {"entries": [{"path": "tools/check_x.py",
                  "line": 'PATTERN = re.compile(r"pkill -f .*App")',
                  "status": "legitimate",
                  "reason": "a checker's own pattern; it matches kills, it runs none"}]}

Each entry excuses the hits in ONE file whose code line equals `line` exactly,
after comment stripping and trimming, so it cannot launder the rest of that
file. Every entry needs a reason. An entry that matches nothing in the scanned
files is STALE and fails the gate: a fixed kill must take its exemption with
it. At --staged only the entries for staged files are judged.

`status` keeps two populations apart. `legitimate` is a decision, and its
reason is the argument someone can challenge. `unreviewed` is debt: a kill
found when the gate was seeded and not examined yet. An entry with no status
is unreviewed, and every run prints the unreviewed count as a warning, so a
list of debt never reads as clean.
"""
from __future__ import annotations  # OS python3 may be 3.9

import argparse
import ast
import io
import json
import os
import re
import sys
import tokenize
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import content_bytes, listed_files, repo_root  # noqa: E402

ALLOW_FILE = "kill_by_name_allow.json"
TAG = "[no_kill_by_name]"
STATUSES = ("legitimate", "unreviewed")

HASH_COMMENT = {".sh", ".bash", ".zsh", ".py", ".rb", ".yml", ".yaml", ".mk", ".pl"}
SLASH_COMMENT = {".rs", ".swift", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go",
                 ".c", ".cc", ".cpp", ".h", ".hpp", ".m", ".mm", ".java", ".kt"}
NAMED = {"Makefile", "makefile", "GNUmakefile", "justfile", "Justfile"}

# A word, not a substring: `pkill` inside `skill_pkill_x` or `pkill.py` is not the command.
_WORD = r"(?<![\w./-]){}(?![\w-])"
KILL_BY_NAME = re.compile(_WORD.format("(?:pkill|killall)"))
LOOKUP = re.compile(_WORD.format("(?:pgrep|pidof)"))
KILL = re.compile(_WORD.format("kill"))
# The flags that tie a pkill or pgrep to an owner rather than a name. Quoted forms included,
# because a Python argv list spells them as "-P".
SCOPED = re.compile(r"""(?<![\w-])["']?(?:-P|-g|-s|--parent|--pgroup|--session)(?![\w-])""")
# `command -v pkill` asks whether the tool exists; it kills nothing.
PROBE = re.compile(r"(?:command\s+-[vV]|which|type(?:\s+-[a-zA-Z]+)?|hash)\s+$")


def in_scope(rel: str, blob: bytes) -> bool:
    name = os.path.basename(rel)
    ext = os.path.splitext(name)[1]
    if name in NAMED or ext in HASH_COMMENT or ext in SLASH_COMMENT:
        return True
    return ext == "" and blob.startswith(b"#!")


def _python_code_lines(text: str) -> dict[int, str] | None:
    """{lineno: code} with comments and docstrings removed, or None if it does not parse."""
    try:
        with warnings.catch_warnings():   # a file's own invalid escapes are not this gate's report
            warnings.simplefilter("ignore")
            tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    prose = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            prose.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    lines = text.split("\n")
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                row, col = tok.start
                lines[row - 1] = lines[row - 1][:col]
    except (tokenize.TokenError, IndentationError):
        return None
    return {i: line for i, line in enumerate(lines, 1) if i not in prose}


def _plain_code_lines(text: str, ext: str) -> dict[int, str]:
    out = {}
    hash_style = ext not in SLASH_COMMENT
    for i, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith(("#", "//", "/*", "*", "--")) and not stripped.startswith("#!"):
            continue
        cut = re.search(r"\s#" if hash_style else r"\s//", line)
        out[i] = line[:cut.start()] if cut else line
    return out


def code_lines(rel: str, text: str) -> dict[int, str]:
    ext = os.path.splitext(rel)[1]
    if ext == ".py":
        parsed = _python_code_lines(text)
        if parsed is not None:
            return parsed
    return _plain_code_lines(text, ext)


def finding(code: str) -> str | None:
    """Why this line kills by name, or None."""
    for m in KILL_BY_NAME.finditer(code):
        if PROBE.search(code[:m.start()]):
            continue
        if m.group(0) == "pkill" and SCOPED.search(code[m.end():]):
            continue
        return f"{m.group(0)} matches by name"
    m = LOOKUP.search(code)
    if m and KILL.search(code) and not SCOPED.search(code[m.end():]):
        return f"a {m.group(0)} lookup feeds a kill"
    return None


def scan(root: str, files: list[str], staged: bool) -> tuple[list[tuple[str, int, str, str]], int]:
    hits, checked = [], 0
    for rel in files:
        if os.path.basename(rel) == ALLOW_FILE:
            continue
        blob = content_bytes(root, rel, staged=staged)
        if blob is None or b"\0" in blob[:4096] or not in_scope(rel, blob):
            continue
        checked += 1
        text = blob.decode("utf-8", errors="replace")
        for lineno, code in sorted(code_lines(rel, text).items()):
            why = finding(code)
            if why:
                hits.append((rel, lineno, code.strip(), why))
    return hits, checked


def load_allow(root: str, staged: bool) -> tuple[list[dict], list[str]]:
    """(entries, problems). An absent file is an empty list; a malformed one is a problem."""
    blob = content_bytes(root, ALLOW_FILE, staged=staged)
    if blob is None:
        return [], []
    try:
        data = json.loads(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return [], [f"{ALLOW_FILE} is not valid JSON: {exc}"]
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return [], [f'{ALLOW_FILE} must be {{"entries": [...]}}']
    problems = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or not all(isinstance(e.get(k), str) and e[k].strip()
                                              for k in ("path", "line", "reason")):
            problems.append(f"{ALLOW_FILE} entry {i} needs a non-empty path, line and reason")
        elif e.get("status", "unreviewed") not in STATUSES:
            problems.append(f"{ALLOW_FILE} entry {i}: status must be one of {', '.join(STATUSES)}")
    return entries, problems


def judge(hits, entries, scanned: set[str]):
    """(unexcused hits, stale entries, indices of the entries that excused a hit).

    A duplicate entry is stale: the first of two identical entries takes every match."""
    used = set()
    left = []
    for rel, lineno, code, why in hits:
        match = next((i for i, e in enumerate(entries)
                      if e.get("path") == rel and e.get("line", "").strip() == code), None)
        if match is None:
            left.append((rel, lineno, code, why))
        else:
            used.add(match)
    stale = [e for i, e in enumerate(entries) if i not in used and e.get("path") in scanned]
    return left, stale, used


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--exclude", default="", help="regex; matching repo-relative paths are skipped")
    args = ap.parse_args()
    skip = re.compile(args.exclude) if args.exclude else None
    root = repo_root()
    scope = "staged" if args.staged else "tracked"
    files = [f for f in listed_files(root, staged=args.staged) if not (skip and skip.search(f))]
    entries, problems = load_allow(root, args.staged)
    if problems:
        for p in problems:
            print(f"✗ {TAG} {p}")
        return 1
    hits, checked = scan(root, files, args.staged)
    left, stale, used = judge(hits, entries, set(files))
    if left:
        print(f"✗ {TAG} {len(left)} process kill(s) by name in {scope} code:")
        for rel, lineno, code, why in left[:40]:
            print(f"    {rel}:{lineno}: {code[:140]}  ({why})")
        print("    kill what you started: its pid, its process group (pkill -g, killpg), or the")
        print("    tree below your own pid (pkill -P). A name matches processes you do not own.")
        print(f"    A kill that must stand goes in {ALLOW_FILE} with a reason.")
    if stale:
        print(f"✗ {TAG} {len(stale)} stale entr(ies) in {ALLOW_FILE}: the kill is gone, the exemption stayed:")
        for e in stale:
            print(f"    {e.get('path')}: {e.get('line')}")
    if left or stale:
        return 1
    if not checked:
        # Nothing staged is a fact about this commit. Nothing TRACKED is a fact about the gate:
        # its scope stopped matching the tree, and "0 files clean" would read like a clean tree.
        if args.staged:
            print(f"✓ {TAG} nothing staged — 0 code files to check")
            return 0
        print(f"✗ {TAG} nothing to check (tracked) — refusing to report clean over zero code files")
        return 1
    note = f", {len(hits)} allowlisted in {ALLOW_FILE}" if hits else ""
    print(f"✓ {TAG} OK — {checked} {scope} code files, no kill by name{note}")
    debt = sum(1 for i in used if entries[i].get("status", "unreviewed") == "unreviewed")
    if debt:
        print(f"⚠ {TAG}   {debt} of them 'unreviewed': found when the gate was seeded, not yet decided")
    return 0


if __name__ == "__main__":
    sys.exit(main())
