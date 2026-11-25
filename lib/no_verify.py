"""A `git commit` or `git push` that skips the hooks is refused before it runs (GOH.NOVERIFYGUARD).

THE CASE (2026-11-17). GOH.PREPARITY lane 2 made two commits with `git commit --no-verify`; the
bash-5 guard its new `gates/_proven_identity.sh` lacked was caught by the enqueue pre-step (200 s)
instead of the commit hook (seconds). The loop's lane rules already said never (`loopedit.py`), and
a rule nobody enforces does not hold.

THE RULE, in the CODE of a Bash command (`lib/shell_segments.py`: quoted text, heredoc bodies and
comments are not commands). Refused: `git commit` or `git push` with `--no-verify`; `git commit`
with `-n` (alone or in a short-option cluster before a letter that takes a value: `-anm x`); and
any git verb run with `-c core.hooksPath=...` (case-insensitive). An option's VALUE is not an
option (`git commit -m -n`, `-m 'mentions --no-verify'`), nothing after `--` is, and push's `-n`
is `--dry-run`. The escape is `# no-verify-ok: <reason>`.

The discard guard's refusal of a `git commit`/`git push` that skips the hooks: `--no-verify`,
commit's `-n` (alone or in a short-option cluster), or `-c core.hooksPath=...` on the command line;
an option's value, a quoted message and anything after `--` are not flags, and push's `-n` is
`--dry-run`. GOH.PREPARITY's two `--no-verify` commits carried a defect the commit hook would have
caught in seconds, found by the enqueue pre-step 200 s later (GOH.NOVERIFYGUARD). Escape:
"""

from __future__ import annotations

import os
import re

import shell_segments

ESCAPE = re.compile(r"#\s*no-verify-ok:\s*\S")
# commit's options that take their value as the next word (or glued to the short letter).
SHORT_VALUED = set("mFCcSt")
LONG_VALUED = {
    "--message", "--file", "--author", "--date", "--template", "--reuse-message",
    "--reedit-message", "--fixup", "--squash", "--trailer", "--cleanup", "--pathspec-from-file",
}  # fmt: skip
PUSH_VALUED = {"--repo", "--receive-pack", "--exec", "--push-option", "-o"}
REASON = (
    "`{shown}` skips the git hooks, the gate that refuses a defect in seconds: GOH.PREPARITY's two "
    "`--no-verify` commits carried a defect the commit hook would have caught, found instead by "
    "the enqueue pre-step 200 s later. Commit with the hooks and fix what they refuse. To skip "
    "them on purpose, add `# no-verify-ok: <reason>` to the command."
)


def _skips(verb: str, args: list[str]) -> bool:
    valued = PUSH_VALUED if verb == "push" else LONG_VALUED
    i = 0
    while i < len(args):
        word = args[i]
        i += 1
        if word == "--":
            return False
        if word == "--no-verify":
            return True
        if word.startswith("--"):
            if "=" not in word and word in valued:
                i += 1
            continue
        if verb != "commit" or not word.startswith("-") or word == "-":
            continue
        for pos, letter in enumerate(word[1:]):
            if letter == "n":
                return True
            if letter in SHORT_VALUED:
                if pos == len(word) - 2:  # the value is the next word
                    i += 1
                break
    return False


def judge(command: str) -> str | None:
    """The refusal for a git commit/push that skips the hooks, or None."""
    if ESCAPE.search(command):
        return None
    try:
        segs = shell_segments.segments(command)
    except ValueError:
        return None
    for seg in segs:
        name, rest = shell_segments.program(seg)
        if name != "git":
            continue
        hooks_off = False
        while rest and rest[0].startswith("-"):
            opt = rest.pop(0)
            if opt in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") and rest:
                value = rest.pop(0)
                if opt == "-c" and value.lower().startswith("core.hookspath="):
                    hooks_off = True
            elif opt.startswith("-c") and opt[2:].lower().startswith("core.hookspath="):
                hooks_off = True
        if not rest:
            continue
        verb = os.path.basename(rest[0])
        if hooks_off or (verb in ("commit", "push") and _skips(verb, rest[1:])):
            return REASON.format(shown=" ".join(seg))
    return None
