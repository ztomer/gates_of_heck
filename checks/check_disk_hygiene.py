#!/usr/bin/env python3
"""Fail when build and test scratch has grown past a sane ceiling.

WHY THIS EXISTS. On 2026-08-23 a development machine hit 100% of a 926GB disk
(2.0GiB free) and began failing unrelated work: a Rust pre-push gate failed for
no reason but lack of space, and passed unchanged once there was room. A gate
failing for a reason that has nothing to do with the diff is the worst kind,
because the obvious next move is to debug the diff.

Two independent leaks, both invisible until measured:

  246GB  $TMPDIR/pytest-of-<user>/  — a test fixture built fake model shards
         with `write_bytes(b"\\0" * size)`, and one call site legitimately
         wanted 15GiB because the size WAS the assertion. Every run wrote a
         real 15GiB file; pytest retains several roots; 26 accumulated.
   30GB  /tmp/koffee-*  — 75 one-off Xcode `-derivedDataPath` scratch trees
         from ad-hoc debugging runs, each ~1GB including its own 225MB
         dependency git clone, none ever removed.

Neither was a logging leak, and neither showed up in any project directory, so
"the repo looks fine" was true and useless. Nothing was watching the places the
bytes actually went.

This check watches them. It is deliberately a CEILING, not a cleaner: deleting
someone's scratch mid-session is worse than telling them about it.

    check_disk_hygiene.py                    # default ceilings
    check_disk_hygiene.py --max-scratch-gb 20
    check_disk_hygiene.py --min-free-gb 25
    check_disk_hygiene.py --warn-only        # report, never fail
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GIB = 1024**3


def _du_bytes(path: Path) -> tuple[int, "str | None"]:
    """(bytes, warning). Disk usage counting ALLOCATED blocks, not apparent
    size — `du` rather than summing st_size: a sparse file reports a huge
    size while occupying nothing.

    Partial-failure honesty (2026-08-25): du exiting nonzero while still
    printing a total (e.g. an unreadable subdirectory) used to be treated as
    NO data and returned 0, so a multi-GB tree with one locked subtree
    reported empty and silently passed its ceiling. Now the PARTIAL total is
    used and a warning names the failure; only EMPTY stdout yields zero.
    """
    try:
        out = subprocess.run(
            ["/usr/bin/du", "-sk", str(path)],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 0, f"du could not run on {path}: {exc}"
    tail = out.stderr.strip()[-200:]
    if out.returncode != 0:
        lines = out.stdout.strip().splitlines()
        if lines:
            try:
                return int(lines[-1].split()[0]) * 1024, (
                    f"du exited {out.returncode} reading {path} — partial "
                    f"measure used ({tail})")
            except (ValueError, IndexError):
                pass
        return 0, (f"du exited {out.returncode} with no usable output for "
                   f"{path}: {tail}")
    if not out.stdout.strip():
        return 0, f"du produced no output for {path}"
    try:
        return int(out.stdout.split()[0]) * 1024, None
    except ValueError:
        return 0, f"du output unparseable for {path}"


def _scratch_roots() -> list[Path]:
    roots = [Path(tempfile.gettempdir()), Path("/tmp")]
    seen, unique = set(), []
    for r in roots:
        try:
            resolved = r.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def _largest_children(root: Path, limit: int = 5) -> list[tuple[str, int]]:
    sized: list[tuple[str, int]] = []
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    # Bound the work: a scratch root with 15,000 entries is itself a symptom,
    # but walking all of them to report the top 5 is not worth the minutes.
    for child in children[:400]:
        size, _warn = _du_bytes(child)
        if size > GIB // 2:
            sized.append((child.name, size))
    sized.sort(key=lambda pair: -pair[1])
    return sized[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-scratch-gb", type=float, default=25.0)
    ap.add_argument("--min-free-gb", type=float, default=20.0)
    ap.add_argument("--warn-only", action="store_true")
    args = ap.parse_args()

    problems: list[str] = []

    free = shutil.disk_usage(os.getcwd()).free / GIB
    if free < args.min_free_gb:
        problems.append(
            f"only {free:.1f}GB free on this volume (floor {args.min_free_gb:.0f}GB). "
            "Builds start failing for reasons that look like code defects."
        )

    for root in _scratch_roots():
        used, du_warn = _du_bytes(root)
        if du_warn:
            print(f"⚠ [disk] {du_warn}", file=sys.stderr)
        used_gb = used / GIB
        # Free space on the scratch root's OWN volume, alongside the
        # cwd-volume check above: a scratch root can live on a different,
        # fuller volume than the repo.
        free_here = shutil.disk_usage(root).free / GIB
        if free_here < args.min_free_gb:
            problems.append(
                f"only {free_here:.1f}GB free on {root}'s volume "
                f"(floor {args.min_free_gb:.0f}GB). "
                "Builds start failing for reasons that look like code defects."
            )
        if used_gb <= args.max_scratch_gb:
            print(f"→ [disk] {root}: {used_gb:.1f}GB (ceiling {args.max_scratch_gb:.0f}GB)")
            continue
        detail = "\n".join(
            f"      {size / GIB:>7.1f}GB  {name}"
            for name, size in _largest_children(root)
        )
        problems.append(
            f"{root} holds {used_gb:.1f}GB of scratch (ceiling "
            f"{args.max_scratch_gb:.0f}GB). Largest:\n{detail}"
        )

    if not problems:
        print(f"→ [disk] OK — {free:.1f}GB free, scratch within ceiling")
        return 0

    label = "warning" if args.warn_only else "FAIL"
    print(f"\n{'⚠' if args.warn_only else '✗'} [disk] {label}:", file=sys.stderr)
    for p in problems:
        print(f"    {p}", file=sys.stderr)
    print(
        "\n  These are scratch directories: build output, test tmp dirs, derived\n"
        "  data. Deleting them costs a rebuild and nothing else. Find the WRITER\n"
        "  before clearing, or it refills by tomorrow.",
        file=sys.stderr,
    )
    return 0 if args.warn_only else 1


if __name__ == "__main__":
    sys.exit(main())
