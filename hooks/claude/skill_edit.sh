#!/usr/bin/env bash
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
# hooks/claude/skill_edit.sh -- a Claude Code PostToolUse hook: judge the skills corpus the moment
# a file in it is written (C2, writer-time half; tests/test_claude_skill_hook.py).
#
# `goh skills` gates the corpus at commit and push, which is sessions after the writer broke it.
# Wired in ~/.claude/settings.json (matcher Write|Edit|MultiEdit); reads the event on stdin. An
# edit outside the corpus ($GOH_SKILLS_ROOT, default ~/.claude/skills) costs one python3 call and
# runs nothing. A finding is exit 2 with the report on stderr: Claude Code hands stderr of an
# exit-2 PostToolUse hook back to the writer, so the fix lands in the same turn.
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
root="${GOH_SKILLS_ROOT:-$HOME/.claude/skills}"
inside="$(python3 -c '
import json, os, sys
try:
    path = json.load(sys.stdin).get("tool_input", {}).get("file_path") or ""
except ValueError:
    sys.exit(0)
if path:
    root = os.path.realpath(sys.argv[1])
    full = os.path.realpath(path)
    if full == root or full.startswith(root + os.sep):
        print(root)
' "$root")" || exit 0
[ -n "$inside" ] || exit 0
if out="$(bash "$here/../../gates/goh.sh" skills --root "$inside" 2>&1)"; then
    exit 0
fi
{
    echo "✗ the skills corpus at $inside is now red (goh skills) -- fix it before moving on:"
    printf '%s\n' "$out"
} >&2
exit 2
} # parse-guard
