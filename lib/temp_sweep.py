"""A recursive rm of a glob reaching goh's temp roots is refused before it runs (GOH.FLAKYENVCOV).

THE CASE (2026-11-14, ~21:45). A lane agent ran `rm -rf $TMPDIR/goh-pytest.*/` to clean up after
itself. Each gate's pytest has a temp root of its own (`gates/_pytest_root.sh`,
`$TMPDIR/goh-pytest.XXXXXX`, one per run, and goh's own pruning, `lib/prune_kept.py`, spares a root
whose `.goh-owner` is alive), but the glob reached every LIVE run's root too: another lane's land
failed with 1281 FileNotFoundError errors across unrelated tests. A run's root is its own only while
nothing sweeps the shared name.

THE RULE, in the CODE of a Bash command (`lib/shell_segments.py`: quoted text, heredoc bodies and
comments are not commands):
  * `rm` with `-r`/`-R`/`--recursive` and an operand whose LAST component is a glob matching a
    goh temp root's name (`goh-pytest.slot00`, `goh-pytest.ab12CD`, `goh-suite-cov-cache.ab12CD`)
    and either names goh
    (`goh-*`, `goh-pytest.*`) or sits in a temp directory (`$TMPDIR/*`, `/tmp/*`, /var/folders);
  * `find` with `-delete` or `-exec rm`, whose `-name`/`-iname` glob matches such a name.
So is `rm -r` of their one parent, `$TMPDIR/goh.noindex` (GOH.TMPNOINDEX), by name or not.
One NAMED root (`rm -rf "$TMPDIR/goh-pytest.ab12CD"`) is one run's and runs. The escape is
`# temp-sweep-ok: <reason>`.
"""

from __future__ import annotations

import fnmatch
import re

import shell_segments

ESCAPE = re.compile(r"#\s*temp-sweep-ok:\s*\S")
GLOB = re.compile(r"[*?\[]")
# The names goh gives its per-run temp roots: a reused slot (gates/_pytest_root.sh, GOH.FSCHURN),
# and a fresh one as a mktemp would spell it.
ROOTS = ("goh-pytest.slot00", "goh-pytest.ab12CD", "goh-suite-cov-cache.ab12CD")
PARENT = "goh.noindex"  # the one parent of them all (lib/tmp_root.sh): removing it is every sweep
TEMP_DIR = re.compile(r"(\$\{?TMPDIR|^/tmp(/|$)|^/private/tmp(/|$)|/var/folders/)")
WAY_OUT = (
    "goh's per-run temp roots ($TMPDIR/goh.noindex/goh-pytest.slotNN) belong to the gates that "
    "made them, and a glob reaches the LIVE ones of every other session (GOH.FLAKYENVCOV: a land "
    "failed with 1281 FileNotFoundError errors). Remove the ONE root your run made, by its full "
    "name, or leave kept roots to lib/prune_kept.py, which spares a live owner's. A deliberate "
    "sweep says so in the command: `# temp-sweep-ok: <reason>`."
)


def _reaches_root(pattern: str, directory: str) -> bool:
    if not GLOB.search(pattern) or not any(fnmatch.fnmatchcase(r, pattern) for r in ROOTS):
        return False
    return pattern.startswith("goh") or bool(TEMP_DIR.search(directory))


def _rm_sweeps(args: list[str], here: str) -> str | None:
    flags = [a for a in args if a.startswith("-")]
    recursive = any(
        a in ("--recursive", "-r", "-R") or (not a.startswith("--") and re.search("[rR]", a[1:]))
        for a in flags
    )
    if not recursive:
        return None
    for operand in (a for a in args if not a.startswith("-")):
        path = operand.rstrip("/")
        directory, _, base = path.rpartition("/")
        if base == PARENT or _reaches_root(base, directory if "/" in path else here):
            return operand
    return None


def _find_sweeps(args: list[str]) -> str | None:
    removes = "-delete" in args or any(
        a in ("-exec", "-execdir") and i + 1 < len(args) and args[i + 1] == "rm"
        for i, a in enumerate(args)
    )
    if not removes:
        return None
    for i, a in enumerate(args[:-1]):
        start = args[0] if args and not args[0].startswith("-") else ""
        if a in ("-name", "-iname") and _reaches_root(args[i + 1], start):
            return args[i + 1]
    return None


def judge(command: str) -> str | None:
    """The refusal for a glob sweep over goh's temp roots, or None."""
    if ESCAPE.search(command):
        return None
    try:
        segments = shell_segments.segments(command)
    except ValueError:
        return None
    here = ""
    for seg in segments:
        prog, args = shell_segments.program(seg)
        if prog == "cd" and len(args) == 1:
            here = args[0]
            continue
        hit = (
            _rm_sweeps(args, here)
            if prog == "rm"
            else _find_sweeps(args)
            if prog == "find"
            else None
        )
        if hit:
            return f"`{' '.join(seg)}`: the glob `{hit}` can reach goh-pytest roots. {WAY_OUT}"
    return None


__all__ = ["ESCAPE", "judge"]
