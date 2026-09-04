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

Third leak (2026-08-26): the shared Rust build cache. `~/.cargo/config.toml`
sets `build.target-dir = "~/.cache/cargo-target"` so every crate shares one
directory. Cargo namespaces per package, so it grows without bound — 17.6 GB
across 8 per-crate target/ dirs was measured while sccache was thought to be
handling it, and the shared dir itself likewise accumulates `debug/incremental`
and `debug/deps` that are never pruned. Per-project `target/` dirs remain for
exempted crates (e.g. `~/Projects/routines/target`). Nothing was watching those
either — same class, new writer.

    check_disk_hygiene.py                    # default ceilings
    check_disk_hygiene.py --max-scratch-gb 20
    check_disk_hygiene.py --min-free-gb 25
    check_disk_hygiene.py --warn-only        # report, never fail
    check_disk_hygiene.py --max-cache-dir-gb 50
    check_disk_hygiene.py --watch-paths ~/.cache/cargo-target:~/Projects/foo/target
    GOH_MAX_CACHE_GB=50 GOH_WATCH_PATHS=~/.cache/cargo-target:~/Projects/foo/target check_disk_hygiene.py
    GOH_SCRATCH_ROOTS=/tmp/qa-scratch  # override scratch roots (tests + scoping)
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

GIB = 1024**3

# du is a stat() storm: single-threaded over N roots costs the SUM. Threads
# share nothing but the result list (du is a subprocess per path), so a small
# pool wins ~N x on distinct trees. Measured 2026-09-04: 15.1s serial over
# $TMPDIR + shared cargo-target + 4 project targets → ~4s pooled.
_DU_WORKERS = 8


def _map_du(paths: list[Path]) -> list[tuple[int, "str | None"]]:
    """_du_bytes over many paths, in input order, du's running pooled."""
    if len(paths) < 2:
        return [_du_bytes(p) for p in paths]
    with ThreadPoolExecutor(max_workers=min(_DU_WORKERS, len(paths))) as pool:
        return list(pool.map(_du_bytes, paths))


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
    # GOH_SCRATCH_ROOTS overrides auto-discovery (same separators as
    # GOH_WATCH_PATHS). Production use: scope the watch to one tree.
    # Test use: point at a fixture dir so e2e runs never scan the host.
    override = _parse_watch_paths(os.environ.get("GOH_SCRATCH_ROOTS"))
    roots = ([Path(p) for p in override] if override is not None
             else [Path(tempfile.gettempdir()), Path("/tmp")])
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
    capped = children[:400]
    for child, (size, _warn) in zip(capped, _map_du(capped)):
        if size > GIB // 2:
            sized.append((child.name, size))
    sized.sort(key=lambda pair: -pair[1])
    return sized[:limit]


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"⚠ [disk] {name}={raw!r} not a number — using {default}", file=sys.stderr)
        return default


def _parse_watch_paths(raw: str | None) -> list[Path] | None:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return []
    # Accept colon, comma, semicolon, or whitespace as separators so both
    # CLI and env var forms are forgiving.
    tokens = re.split(r"[,:;\s]+", raw)
    parts: list[Path] = []
    seen: set[Path] = set()
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        expanded = os.path.expandvars(os.path.expanduser(tok))
        p = Path(expanded)
        try:
            resolved = p.resolve()
        except OSError:
            resolved = p
        if resolved in seen:
            continue
        seen.add(resolved)
        parts.append(p)
    return parts


def _default_cache_watch_paths() -> list[Path]:
    """Default watch list: ~/.cache/cargo-target plus any ~/Projects/*/target that exists."""
    candidates: list[Path] = []
    primary = Path("~/.cache/cargo-target").expanduser()
    candidates.append(primary)
    projects = Path.home() / "Projects"
    try:
        for child in projects.iterdir():
            target = child / "target"
            try:
                if target.is_dir():
                    candidates.append(target)
            except OSError:
                continue
    except OSError:
        pass
    # Deduplicate by resolved path while preserving order and original form.
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in candidates:
        try:
            resolved = p.resolve()
        except OSError:
            resolved = p
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(p)
    return unique


def _watch_paths(args: argparse.Namespace) -> list[Path]:
    # CLI --watch-paths overrides env var which overrides auto-discovery.
    cli_raw = getattr(args, "watch_paths", None)
    if cli_raw is not None:
        parsed = _parse_watch_paths(cli_raw)
        if parsed is not None:
            return parsed
    env_raw = os.environ.get("GOH_WATCH_PATHS")
    if env_raw is not None:
        parsed = _parse_watch_paths(env_raw)
        if parsed is not None:
            return parsed
    return _default_cache_watch_paths()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-scratch-gb", type=float, default=_env_float("GOH_MAX_SCRATCH_GB", 25.0))
    ap.add_argument("--min-free-gb", type=float, default=_env_float("GOH_MIN_FREE_GB", 20.0))
    ap.add_argument("--max-cache-dir-gb", type=float, default=_env_float("GOH_MAX_CACHE_GB", 50.0))
    ap.add_argument("--watch-paths", dest="watch_paths", type=str, default=None,
                    help="colon/comma/space-separated list of cargo cache dirs to watch; "
                         "overrides GOH_WATCH_PATHS and the default (~/.cache/cargo-target plus ~/Projects/*/target)")
    ap.add_argument("--warn-only", action="store_true")
    args = ap.parse_args()

    problems: list[str] = []

    free = shutil.disk_usage(os.getcwd()).free / GIB
    if free < args.min_free_gb:
        problems.append(
            f"only {free:.1f}GB free on this volume (floor {args.min_free_gb:.0f}GB). "
            "Builds start failing for reasons that look like code defects."
        )

    scratch = _scratch_roots()
    # Telling > deleting: missing paths are not an error, just skipped.
    all_watches = _watch_paths(args)
    for watch in all_watches:
        if not watch.exists():
            print(f"→ [disk] {watch}: missing (skipped)")
    watches = [w for w in all_watches if w.exists()]
    # ONE pool over every root: wall time is the SLOWEST root, not the sum
    # (15.1s serial → ~8s pooled, measured 2026-09-04; two pools would still
    # stack the slowest scratch root on the slowest cache dir).
    sizes = _map_du(scratch + watches)
    scratch_sizes = sizes[:len(scratch)]
    watch_sizes = sizes[len(scratch):]
    for root, (used, du_warn) in zip(scratch, scratch_sizes):
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

    # ---- cargo cache watch (same ceiling pattern as scratch) -----------------
    for watch, (used, du_warn) in zip(watches, watch_sizes):
        if du_warn:
            print(f"⚠ [disk] {du_warn}", file=sys.stderr)
        used_gb = used / GIB
        if used_gb <= args.max_cache_dir_gb:
            print(f"→ [disk] {watch}: {used_gb:.1f}GB (ceiling {args.max_cache_dir_gb:.0f}GB)")
            continue
        detail = "\n".join(
            f"      {size / GIB:>7.1f}GB  {name}"
            for name, size in _largest_children(watch)
        )
        # Name the writer class so the fix is obvious without hunting.
        problems.append(
            f"shared CARGO_TARGET_DIR {watch} grew past {args.max_cache_dir_gb:.0f}GB "
            f"({used_gb:.1f}GB) — rm -rf {watch}/debug/incremental is zero-risk first move"
            + (f". Largest:\n{detail}" if detail else "")
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
