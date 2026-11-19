#!/usr/bin/env python3
"""diff_cov.py -- the executable Rust lines a change adds or edits are covered (GOH.DIFFCOV).

THE HOLE. The Rust coverage gate judged a whole-crate floor (gates/lcov_merge.py): a change could
add untested lines while the crate's total stayed above it, so a 97% crate took a 0%-covered new
function and stayed green. This reader judges the CHANGE: every line the diff from the change's
base adds or edits, on its NEW side (`git diff -M`: a renamed or moved file is judged at its new
path and numbers), looked up in the run's final lcov parts. A line with a line record (executable
on this build) whose best count across the parts is 0 is red, named `file:line`. The target is
100% of changed executable lines; a line with no record (a comment, a declaration, a cfg'd-out
arm) is not executable here and not judged. Lines the floor already carried are not this check's.

A SEPARATE READER of the report: it never produces, merges or changes the parts (`part-*.info`,
read with lcov_merge's own parser), so it judges whatever merged report the coverage run makes.
It judges a changed line by its own best count, as the floor does since GOH.SPANFORGIVE removed
the floor's span forgiveness: an untaken arm of a called function is exactly the untested line.

THE BASE, first found: `--base`; GOH_CHANGE_BASE (the push gate's: the remote's tip, so a
consumer's push judges what it pushes); the merge base with the branch's upstream; the merge base
with `main` (a land's export is a detached worktree with no upstream: its change is everything
since main); with origin's default branch; HEAD's parent. No base (a root commit, no HEAD) and no
git checkout are each said and pass: there is no change to judge, the floor still runs.

ZERO INPUTS ARE NO PASS (GOH.DIFFCOVEMPTY): a change with a changed Rust line judged against a
missing or empty parts directory, or parts with no line record, is refused (exit 2) naming the
directory and the unmeasured files; it used to read "no changed executable Rust line" and pass, and
lanes cited that green. A change with no changed Rust line passes without parts, saying so.

THE ALLOWLIST (GOH_COV_DIFF_ALLOW, default `diffcov_allow.json` at the repository's top), never a
code-level suppression: `{"entries": [{"file": REL, "text": STRIPPED_LINE, "reason": WHY}]}`
exempts a changed line of that file whose text is that. A ratchet: an entry is STALE, and red,
when its file is gone, its text is in no line of the file, or every occurrence that has a line
record is covered; an entry whose text has no line record here (another platform's build has it)
is kept. An entry without a reason is refused (exit 2).

THE KNOB, GOH_COV_DIFF: `on` (the default), `warn` (named, never red: a consumer's roll-out),
`off` (said, not run). Rust only: the Swift and C coverage paths are out of its scope.

Usage: diff_cov.py --repo DIR [--base REV] [--include RE] PARTS_DIR
Exit codes: 0 pass | 1 an uncovered changed line or a stale entry | 2 usage, config or input error.
"""

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _cl
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _cl.suppress(ModuleNotFoundError):  # a copy outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "lib"))
import gitrun  # noqa: E402  (lib/: git under a deadline, aimed at its repository)
from lcov_merge import LcovError, parse_part  # noqa: E402  (the one lcov reader)

from tui.lib import counted, err, info, measure, warn  # noqa: E402  (lcov_merge put the root on sys.path)

TAG = "[diff-coverage]"
GIT_TIMEOUT = 120
MODES = ("on", "warn", "off")
HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


class Refused(Exception):
    """A usage, config or input error: exit 2, named."""


def _git(top: str, *args: str) -> str | None:
    r = gitrun.run_reader(list(args), repo=top, timeout=GIT_TIMEOUT)
    return r.stdout if r.returncode == 0 else None


def _commit(top: str, rev: str) -> str | None:
    out = _git(top, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    return out.strip() if out else None


def resolve_base(top: str, given: str) -> tuple[str, str] | None:
    """(base commit, where it came from), or None when the change has no base."""
    named = given or os.environ.get("GOH_CHANGE_BASE", "").strip()
    if named:
        c = _commit(top, named)
        if c is None:
            raise Refused(f"the change base {named} is not a commit in this clone")
        return c, ("--base" if given else "GOH_CHANGE_BASE, the push's remote tip")
    if _commit(top, "@{upstream}"):
        mb = _git(top, "merge-base", "HEAD", "@{upstream}")
        if mb:
            return mb.strip(), "the merge base with the upstream"
    for ref, name in (
        ("refs/heads/main", "main"),
        ("refs/remotes/origin/HEAD", "origin's default branch"),
    ):
        if _commit(top, ref):
            mb = _git(top, "merge-base", "HEAD", ref)
            if mb:
                return mb.strip(), f"the merge base with {name}"
    parent = _commit(top, "HEAD^")
    if parent:
        return parent, "HEAD's parent"
    return None


def changed_lines(top: str, base: str) -> dict[str, set[int]]:
    """repo-relative path -> the new-side line numbers the change adds or edits (the working tree
    against `base`, renames followed, untracked files whole)."""
    out = _git(
        top, "-c", "core.quotePath=false", "diff", "-U0", "--no-color", "--no-ext-diff",
        "--no-textconv", "--no-prefix", "-M", "--diff-filter=d", base, "--", ":(glob)**/*.rs",
    )  # fmt: skip
    if out is None:
        raise Refused(f"git diff from {base} failed")
    changed: dict[str, set[int]] = defaultdict(set)
    cur = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            cur = None if line[4:] == "/dev/null" else line[4:]
        elif cur is not None and line.startswith("@@"):
            m = HUNK.match(line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                changed[cur].update(range(start, start + count))
    others = _git(top, "ls-files", "--others", "--exclude-standard", "-z", "--", ":(glob)**/*.rs")
    for rel in (others or "").split("\0"):
        if rel:
            n = _line_count(os.path.join(top, rel))
            changed[rel].update(range(1, n + 1))
    return changed


def _line_count(path: str) -> int:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def _lines_of(path: str) -> list[str] | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return [ln.strip() for ln in handle.read().splitlines()]
    except OSError:
        return None


def load_allow(top: str) -> tuple[str, list[dict]]:
    raw = os.environ.get("GOH_COV_DIFF_ALLOW", "").strip() or "diffcov_allow.json"
    path = raw if os.path.isabs(raw) else os.path.join(top, raw)
    if not os.path.exists(path):
        if os.environ.get("GOH_COV_DIFF_ALLOW", "").strip():
            raise Refused(f"the allowlist GOH_COV_DIFF_ALLOW names no file: {path}")
        return path, []
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot read the allowlist {path}: {exc}") from exc
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise Refused(f"the allowlist {path} is not an object with an `entries` list")
    for i, e in enumerate(entries):
        ok = isinstance(e, dict) and all(
            isinstance(e.get(k), str) and e[k].strip() for k in ("file", "text", "reason")
        )
        if not ok:
            raise Refused(
                f"the allowlist {path}: entry {i} needs a non-empty file, text and reason "
                f"(an unreachable line is named WITH its reason): {e!r}"
            )
    return path, entries


def report(
    parts: list[str], parts_dir: str, changed: dict[str, set[int]], top: str, include: str
) -> dict[tuple[str, int], int]:
    """(repo-relative path, line) -> the best count across the parts, for files under `top`.
    Refused when the parts hold no line record at all: zero inputs measured nothing."""
    line_best: dict = defaultdict(int)
    try:
        for pf in parts:
            parse_part(pf, line_best)
    except LcovError as exc:
        raise Refused(str(exc)) from exc
    if not line_best:  # GOH.DIFFCOVEMPTY: a report of nothing judges no changed line, so no pass
        touched = sorted(rel for rel, lns in changed.items() if lns)
        files = ", ".join(touched[:3]) + (", ..." if len(touched) > 3 else "")
        lines = sum(len(v) for v in changed.values())
        what = (
            f"no part-*.info in {parts_dir}"
            + ("" if os.path.isdir(parts_dir) else " (no such directory)")
            if not parts
            else f"{counted(len(parts), 'part')} in {parts_dir} hold no line record"
        )
        raise Refused(
            f"{what}: the change's {counted(lines, 'changed Rust line')} ({files}) were never "
            "measured -- run the coverage step that writes the parts first"
        )
    inc = None
    if include:
        try:
            inc = re.compile(include)
        except re.error as exc:
            raise Refused(f"bad --include regex: {exc}") from exc
    real_top = os.path.realpath(top)
    rel_of: dict[str, str | None] = {}
    by_rel: dict[tuple[str, int], int] = {}
    for (sf, ln), cnt in line_best.items():
        if inc is not None and not inc.search(sf):
            continue
        if sf not in rel_of:
            rp = os.path.realpath(sf if os.path.isabs(sf) else os.path.join(top, sf))
            rel = os.path.relpath(rp, real_top)
            rel_of[sf] = None if rel.startswith("..") else rel
        rel = rel_of[sf]
        if rel is not None:
            by_rel[(rel, ln)] = max(by_rel.get((rel, ln), 0), cnt)
    return by_rel


def stale_entries(top: str, entries: list[dict], counts: dict[tuple[str, int], int]) -> list[str]:
    stale = []
    for e in entries:
        rel, text = e["file"], e["text"].strip()
        lines = _lines_of(os.path.join(top, rel))
        if lines is None:
            stale.append(f"{rel}: the file is gone ({text!r})")
            continue
        at = [i + 1 for i, t in enumerate(lines) if t == text]
        if not at:
            stale.append(f"{rel}: no line reads {text!r}")
            continue
        measured = [counts[(rel, ln)] for ln in at if (rel, ln) in counts]
        if measured and all(c > 0 for c in measured):
            stale.append(f"{rel}: {text!r} is covered now")
    return stale


def judge(args: argparse.Namespace) -> int:
    mode = os.environ.get("GOH_COV_DIFF", "").strip() or "on"
    if mode not in MODES:
        raise Refused(f"GOH_COV_DIFF={mode!r}: one of {', '.join(MODES)}")
    if mode == "off":
        info(f"{TAG} off (GOH_COV_DIFF=off): changed Rust lines are not judged")
        return 0
    top_out = _git(args.repo, "rev-parse", "--show-toplevel")
    if top_out is None:
        info(f"{TAG} {args.repo} is not a git checkout: no change to judge")
        return 0
    top = top_out.strip()
    allow_path, entries = load_allow(top)
    based = resolve_base(top, args.base)
    if based is None:
        warn(f"{TAG} no change base (a root commit, or no HEAD): no change to judge")
        return 0
    base, where = based
    changed = changed_lines(top, base)
    said = f"base {base[:12]}, {where}"
    if not any(changed.values()):
        # Nothing to judge, so nothing had to be measured: the one pass that needs no parts.
        measure(f"{TAG} no changed Rust line ({said})")
        return 0
    parts = sorted(glob.glob(os.path.join(args.parts_dir, "part-*.info")))
    counts = report(parts, args.parts_dir, changed, top, args.include)

    executable = uncovered = allowed = 0
    misses = []
    for rel in sorted(changed):
        text_of = None
        for ln in sorted(changed[rel]):
            if (rel, ln) not in counts:
                continue
            executable += 1
            if counts[(rel, ln)] > 0:
                continue
            if text_of is None:
                text_of = _lines_of(os.path.join(top, rel)) or []
            text = text_of[ln - 1] if ln <= len(text_of) else ""
            if any(e["file"] == rel and e["text"].strip() == text for e in entries):
                allowed += 1
                continue
            uncovered += 1
            misses.append(f"    {rel}:{ln}  {text}")
    stale = stale_entries(top, entries, counts)

    say = err if mode == "on" else warn  # both on stderr, with their details under them
    if misses:
        say(
            f"{TAG} {uncovered} of {counted(executable, 'changed executable line')} "
            f"uncovered ({said}):"
        )
        print("\n".join(misses), file=sys.stderr)
        print(
            f"    cover each, or name an unreachable one with its reason in "
            f"{os.path.relpath(allow_path, top)} -- never a code-level suppression",
            file=sys.stderr,
        )
    else:
        tail = f", {allowed} allowlisted" if allowed else ""
        if executable:
            n = f"{executable - allowed}/{executable}"
            measure(f"{TAG} {n} changed executable lines covered ({said}{tail})")
        else:
            measure(f"{TAG} no changed executable Rust line ({said})")
    if stale:
        say(f"{TAG} {counted(len(stale), 'allowlist entry', 'allowlist entries')} stale, delete:")
        print("\n".join(f"    {s}" for s in stale), file=sys.stderr)
    if mode == "warn":
        return 0
    return 1 if misses or stale else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("parts_dir", help="directory holding the run's part-*.info exports")
    ap.add_argument("--repo", default=".", help="the crate's checkout (its git top is the root)")
    ap.add_argument("--base", default="", help="the change's base commit (else resolved)")
    ap.add_argument("--include", default="", help="positive filter regex, as the floor's")
    args = ap.parse_args(argv)
    if not args.include and os.environ.get("GOH_COV_INCLUDE_RE"):
        args.include = os.environ["GOH_COV_INCLUDE_RE"]
    try:
        return judge(args)
    except Refused as exc:
        err(f"{TAG} {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
