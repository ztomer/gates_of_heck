"""gates/_lcov_floor_maps.py -- the crate and file floor maps of lcov_merge.py (GOH.CRATEFLOOR).

A repo that holds a uniform floor (`--floor N`, `file_floor`) and has ONE crate or file it cannot
yet meet used to lower the uniform floor for everything (95 -> 73.7 for 12 of 29 crates; a per-file
74 -> 62.3 for one file). The floors file now names only what it lowers:

    "crate_floors": {"sqlite-snapshot-rs": {"floor": 73, "reason": "why, and what raises it"}},
    "file_floors":  {"crates/x/src/cli_models.rs": {"floor": 62.3, "reason": "..."}}

An entry is a RATCHET, so a stale one is a rug: it must carry a reason, must LOWER the uniform floor
(at or above it, it names nothing), and must name a crate (a Cargo.toml's directory or package name
anywhere in the repo) or a file (on disk) that exists. Judged here, apart from the merger's line
cap. A crate is the nearest enclosing Cargo.toml of a measured file.

A FLOOR WITHIN THE MEASURED SPREAD IS REFUSED (GOH.COVNONDET). One tree measured more than once can
move: monitor's ratchet (2026-11-18) read agent 90.35 to 90.50, multitop 89.67 to 89.82 and vault
90.52 to 90.76, and a floor set from one run (multitop 89.74) went red on the push gate's
clean-tree run (89.67). An entry may record its runs of one tree, `"measured": [89.74, 89.67,
89.82]` (two or more percents); a floor above the lowest of them is refused (exit 2), since a run
at that low end reds an unchanged tree. goh's own number does not move: two ship-shaped
instrumented runs of 2c9ae35f (2026-11-20, load 34-46) covered the same 26381 of 28310 lines, line
for line, and lib/prof_carry.py's 22 fresh-against-claim compares agree at a band of 0.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tomllib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, measure

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "checks")
)
from _gitutil import tree_files


def load_entries(raw, key: str, path: str):
    """`{name: (floor, reason)}` from one map of a floors file, or None after saying why (REFUSED,
    never dropped: an entry that reads like a gate and applies to nothing is the defect)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        err(f"[coverage] {path}: `{key}` is not an object of entries")
        return None
    out = {}
    for name, entry in raw.items():
        floor = entry.get("floor") if isinstance(entry, dict) else None
        reason = entry.get("reason") if isinstance(entry, dict) else None
        if not isinstance(floor, (int, float)) or isinstance(floor, bool) or not 0 <= floor <= 100:
            err(f"[coverage] {path}: {key}[{name!r}] needs a numeric `floor` in 0..100")
            return None
        if not isinstance(reason, str) or not reason.strip():
            err(f"[coverage] {path}: {key}[{name!r}] needs a `reason` (why it is lowered)")
            return None
        if "measured" in entry and (why := _within_spread(key, name, float(floor), entry)):
            err(f"[coverage] {path}: {why}")
            return None
        out[name] = (float(floor), reason.strip())
    return out


def _percent(r) -> bool:
    return isinstance(r, (int, float)) and not isinstance(r, bool) and 0 <= r <= 100


def _within_spread(key: str, name: str, floor: float, entry: dict) -> str:
    """Why an entry's floor stands inside the spread its `measured` runs record, or ''."""
    runs = entry["measured"]
    if not (isinstance(runs, list) and len(runs) >= 2 and all(map(_percent, runs))):
        return (
            f"{key}[{name!r}]: `measured` needs two or more runs of one tree, each a percent in "
            f"0..100 (got {runs!r})"
        )
    lo, hi = min(runs), max(runs)
    if floor <= lo:
        return ""
    return (
        f"{key}[{name!r}] floor {floor:g}% is above the lowest of its measured runs "
        f"({lo:g}..{hi:g}%), {len(runs)} runs of one tree: a run at {lo:g}% reds an unchanged "
        f"tree -- set the floor at or below {lo:g}%"
    )


def not_lowering(entries, uniform: float, key: str) -> list[str]:
    """The entries whose floor is at or above the uniform floor: they lower nothing."""
    return [
        f"{key}[{n!r}] floor {fl:g}% is at or above the uniform floor {uniform:g}% (delete it)"
        for n, (fl, _reason) in sorted(entries.items())
        if fl >= uniform
    ]


def _cargo_dir_of(path: str, cache: dict) -> str | None:
    d = os.path.dirname(os.path.realpath(path))
    chain = []
    while True:
        if d in cache:
            hit = cache[d]
            break
        chain.append(d)
        if os.path.isfile(os.path.join(d, "Cargo.toml")):
            hit = d
            break
        parent = os.path.dirname(d)
        if parent == d:
            hit = None
            break
        d = parent
    for c in chain:
        cache[c] = hit
    return hit


def _names_of(cargo_dir: str) -> set[str]:
    names = {os.path.basename(cargo_dir)}
    try:
        with open(os.path.join(cargo_dir, "Cargo.toml"), "rb") as fh:
            pkg = tomllib.load(fh).get("package", {}).get("name")
    except (OSError, tomllib.TOMLDecodeError):
        pkg = None
    if isinstance(pkg, str):
        names.add(pkg)
    return names


def crates_of(files) -> dict[str, set[str]]:
    """Each measured file's crate: {file: the names it answers to (directory, package)}; a file
    under no Cargo.toml has none."""
    cache: dict = {}
    by_dir: dict = {}
    out: dict[str, set[str]] = {}
    for f in files:
        d = _cargo_dir_of(f, cache)
        out[f] = set() if d is None else by_dir.setdefault(d, _names_of(d))
    return out


def repo_crate_names(cwd: str) -> set[str]:
    """Every crate of the repo the merger runs in -- a repo gated crate by crate measures one of
    them per run, and an entry for another is not stale. The repo is the git top of `cwd` (else
    `cwd`), listed the way every checker lists a tree (checks/_gitutil.py: git's listing, never a
    descent of `target/`)."""
    top = cwd
    with contextlib.suppress(OSError, subprocess.CalledProcessError):
        top = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            stdin=subprocess.DEVNULL,
        ).stdout.strip()
    names: set[str] = set()
    for rel in tree_files(top):
        if os.path.basename(rel) == "Cargo.toml":
            names |= _names_of(os.path.dirname(os.path.join(top, rel)))
    return names


def judge_crates(crates, floor, per_file, missed, crate_names, cwd: str):
    """What `crate_floors` decides. `per_file`: {file: (covered, total)}; `missed`: {file: [lines]};
    `crate_names`: from crates_of. Returns (failures, rest): the messages that fail the run, and the
    files the UNIFORM floor still judges (None when no crate_floors are set)."""
    if not crates:
        return [], None
    if floor is None:
        return [
            "crate_floors lowers the uniform floor (--floor / GOH_COV_FLOOR_RUST): none is set"
        ], None
    fails = not_lowering(crates, floor, "crate_floors")
    known = repo_crate_names(cwd)
    fails += [
        f"crate_floors[{n!r}] names no crate of this repo (stale: delete it)"
        for n in sorted(crates)
        if n not in known
    ]
    by_name: dict[str, list[str]] = {}
    rest = []
    for f in per_file:
        hit = sorted(n for n in crates if n in crate_names.get(f, ()))
        if len(hit) > 1:
            fails.append(f"crate_floors names one crate twice: {', '.join(hit)}")
        if hit:
            by_name.setdefault(hit[0], []).append(f)
        else:
            rest.append(f)
    for n, files in sorted(by_name.items()):
        cov = sum(per_file[f][0] for f in files)
        tot = sum(per_file[f][1] for f in files)
        pct = round(100.0 * cov / tot, 2) if tot else 100.0
        fl, why = crates[n]
        line = (
            f"[coverage] crate {n}: {pct}% of coverable lines "
            f"(floor {fl:g}%, {cov}/{tot} lines; lowered: {why})"
        )
        (err if pct < fl else measure)(line)
        if pct < fl:
            fails.append(f"crate {n} is at {pct}%, below its floor {fl:g}%")
            for f in sorted(files):
                if missed.get(f):
                    print(f"  {f}: {', '.join(map(str, missed[f]))}")
    return fails, rest
