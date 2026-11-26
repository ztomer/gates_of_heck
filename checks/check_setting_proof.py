#!/usr/bin/env python3
"""A change to QoS, worker counts, shares or slots carries before AND after readings, lands AND
lanes (GOH.SETTINGPROOF).

    checks/check_setting_proof.py staged MSGFILE [ROOT]   # commit-msg: the staged diff, MSGFILE
    checks/check_setting_proof.py [range [BASE] [ROOT]]   # each commit of BASE..HEAD: a land step
    checks/check_setting_proof.py read --before FROM..TO --after FROM..TO [--repo R] [--lane-kind K]
                                                          # the two trailers, read from goh.db

THE INCIDENT. Until 2026-11-25 a lane- or work-class gate ran under `taskpolicy -c utility`
(lib/land_share.py, GOH.QOSCLAMP): a contended lane fell from 77-89 units a second to 0.3-3, a
x9-25 slowdown nobody measured for days, because the only figure anyone read was the land's. A
setting moves BOTH sides of the box: what it buys the land path it may take from the lanes.

THE SURFACE is `SURFACE` below, the one declared list: each entry a file and a pattern that names
its setting -- the QoS clamp (taskpolicy, nice, ionice), worker counts and shares, gate slots and
PRE_SLOTS. A diff line (added or removed, `-U0`) matching an entry's pattern touches it, and so
does every changed line inside a function an entry names (git's hunk header carries the
enclosing `def`). A path not on the list is not judged. Every entry must still
match its file in goh's tree (`stale`, at `range`): a setting renamed or moved off the list is red,
never silently unjudged.

THE READINGS are two commit-message trailers, each with both sides on comparable conditions:

    Reading-lands: kind=land repo=R: before 526 s n=8 [..]; after 480 s n=6 [..]; load/core < 1.5
    Reading-lanes: kind=suite class=lane: before 90 s n=3 [..]; after 88 s n=3 [..]; load/core < 1.5

Lands: the median wall of goh.db's `land` spans (tools/land.sh: lock taken to exit). Lanes: the
median wall of spans run by a lane-class gate -- `suite` by default (tools/pytest.sh's whole run,
its `slot_class` the class it was admitted as; one whole run in a lane worktree is the reading),
or `push` for a consumer lane's push gate. Each side counts only runs whose start load per core
was under lib/long_pole.py QUIET_LOAD, the line every quiet reading in goh uses, so a before
taken on a quiet box is never set against an after taken under load. `read` prints both lines.

WHAT IS CHECKED: presence and shape (`before <s> s n=<k>`, `after <s> s n=<k>`, `load/core < x`,
n at least 1 each side) -- not that the figures are true; as with `goh red-run`, a reading's truth
is the reviewer's. No escape: like `goh red-run` this rule has none. A `range` that cannot resolve
BASE (a tree with no main) says it judged nothing. Merge commits are not read (a lane rebases).
Python in checks/, not a native check (docs/new-checker.md): it is goh's own rule over goh's own
surface, and its `read` half reads goh.db through lib/span_store.py. Calibrated by
tests/test_check_setting_proof.py, which plants each refusal and watches it go red.
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

import argparse
import datetime as dt
import re
import statistics
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "lib")]
from tui.lib import counted, err, ok, warn  # noqa: E402 -- the root is on the path from the line above

sys.path.insert(0, str(ROOT / "checks"))
import _gitutil  # noqa: E402 -- checks/, on the path from the line above

TAG = "[setting_proof]"
KEYS = ("Reading-lands", "Reading-lanes")


@dataclass(frozen=True)
class Setting:
    path: str
    pattern: str  # matched against a changed line
    what: str
    function: bool = False  # the pattern names functions: a changed line inside one touches it


# THE SURFACE: every file and constant that sets QoS/taskpolicy/nice, worker counts, worker shares,
# gate slots and PRE_SLOTS. Not on it, not judged; on it and no longer in its file, red (`stale`).
SURFACE: tuple[Setting, ...] = (
    Setting(
        ".gatesrc",
        r"^\s*(export\s+)?(GOH_CI_JOBS|GOH_LAND_SHARE|GOH_GATE_SLOTS"
        r"|GOH_GATE_SLOT_STARVE_MIN|GOH_LANE_QOS|GOH_SUITE_CORES|GOH_GATE_WORKERS)=",
        "the land gate's step concurrency, the land path's share, slots, QoS, worker pins",
    ),
    Setting(
        "lib/land_share.py",
        r"^(LAND_PATH|CLAMPED|SHARE_DEFAULT|DARWIN_CLAMP|LINUX_NICE"
        r"|LINUX_IONICE|SUITELESS_SEED|WIDTH_WINDOW)\s*=",
        "the share and the QoS clamp",
    ),
    Setting(
        "lib/land_share.py",
        r"^def (fraction|weighted|share_of|real_width|qos_prefix|clamp)\(",
        "how the share is split and which classes are clamped",
        function=True,
    ),
    Setting(
        "lib/worker_share.py",
        r"^def (cores|divide|counted|share|own|part_of|live)\(",
        "a gate's worker count",
        function=True,
    ),
    Setting(
        "lib/gate_lanes.py",
        r"^(PRE_SLOTS|CLASSES|STARVE_MIN|RELEASE_RANK"
        r"|PROMOTED_BACKGROUND)\s*=|^\s+\"(short|pre|land|ship|lane|work)\": \(",
        "the slot lanes, PRE_SLOTS and the class ranks",
    ),
    Setting(
        "lib/gate_lanes.py",
        r"^def (lane_slots|default_class)\(",
        "a lane's slots, a class",
        function=True,
    ),
    Setting(
        "lib/gate_slot.py",
        r"^def default_slots\(",
        "the gate slots: one per eight cores",
        function=True,
    ),
    Setting("lib/gate_slot.sh", r"GATE_SLOT_QOS|goh_workers_export", "a gate's clamp and width"),
    Setting("lib/land_slot.py", r"^KEEP\s*=", "the land slots kept warm"),
    Setting("tools/load.py", r"land_share\.clamp|qos_prefix", "a load.py run's QoS"),
    Setting(
        "tools/pytest.sh",
        r"GATE_SLOT_QOS|goh_share_pool|\[ \"\$cores\" -gt",
        "the suite's clamp and worker ceiling",
    ),
)

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@ ?(.*)$")
SIDE = re.compile(r"\b(before|after) (\d+(?:\.\d+)?) s n=(\d+)\b")
LOAD = re.compile(r"\bload/core < \d+(?:\.\d+)?\b")
HOW = (
    "take both with `python3 checks/check_setting_proof.py read --before FROM..TO --after "
    "FROM..TO` "
    "and paste its two lines into the message: the lands side is goh.db's `land` spans; the lanes "
    "side needs a lane-class run on each side of the change -- a whole `tools/pytest.sh` in a lane "
    "worktree (class lane), once under the old setting and once under the new"
)


def touched(diff: str) -> list[tuple[Setting, str]]:
    """(setting, the changed line) for each surface entry a `-U0` unified DIFF touches."""
    hits: list[tuple[Setting, str]] = []
    path, ctx, header = "", "", False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            header = True  # file headers until the first hunk: a removed `-- x` line is no header
            continue
        if header and (line.startswith(("+++ ", "--- "))):
            name = line[4:].strip()
            if name != "/dev/null":
                path = name[2:] if name[:2] in ("a/", "b/") else name
            continue
        if m := HUNK.match(line):
            ctx, header = m.group(1), False
            continue
        if header or line[:1] not in ("+", "-"):
            continue
        body = line[1:]
        for s in SURFACE:
            if s.path == path and (
                re.search(s.pattern, body) or (s.function and re.search(s.pattern, ctx))
            ):
                hits.append((s, f"{path}: {body.strip()[:80]}"))
                break
    return hits


def trailers(message: str) -> dict[str, str]:
    """The reading trailers of MESSAGE (git's `#` comment lines dropped), the last of each kept."""
    got: dict[str, str] = {}
    for line in message.splitlines():
        if line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep and key.strip() in KEYS:
            got[key.strip()] = value.strip()
    return got


def problems(message: str) -> list[str]:
    """What MESSAGE lacks of the two readings; empty when both are whole."""
    got, out = trailers(message), []
    for key in KEYS:
        value = got.get(key)
        if value is None:
            out.append(f"no `{key}:` trailer")
            continue
        sides = {m.group(1): int(m.group(3)) for m in SIDE.finditer(value)}
        for side in ("before", "after"):
            if side not in sides:
                out.append(f"`{key}:` has no `{side} <seconds> s n=<runs>`")
            elif sides[side] < 1:
                out.append(f"`{key}:` {side} reads no run (n=0): nothing was measured")
        if not LOAD.search(value):
            out.append(f"`{key}:` names no `load/core < X`: its sides' conditions are unknown")
    return out


def verdict(diff: str, message: str) -> list[str]:
    """The refusal's lines for one change, empty when it passes or touches no setting."""
    hits = touched(diff)
    if not hits:
        return []
    missing = problems(message)
    if not missing:
        return []
    where = "; ".join(f"{h} ({s.what})" for s, h in hits[:3])
    more = f" and {len(hits) - 3} more" if len(hits) > 3 else ""
    gaps = "; ".join(missing)
    return [f"a settings change ({where}{more}) carries no whole reading: {gaps} -- {HOW}"]


def stale(root: Path) -> list[str]:
    """Surface entries whose pattern no longer matches their file in ROOT."""
    out = []
    for s in SURFACE:
        try:
            text = (root / s.path).read_text(encoding="utf-8")
        except OSError:
            out.append(f"{s.path}: on the surface but not in the tree -- move its entry with it")
            continue
        if not any(re.search(s.pattern, line) for line in text.splitlines()):
            out.append(f"{s.path}: no line matches /{s.pattern}/ -- the setting moved or was "
                       "renamed; the surface (SURFACE in this file) must follow it")  # fmt: skip
    return out


def _git(root: Path, *args: str) -> str:
    """A read-only git in ROOT under a deadline, no optional lock (checks/_gitutil.py git_read);
    CalledProcessError when it fails."""
    done = _gitutil.git_read(["-C", str(root), *args], timeout=120)
    if done.returncode != 0:
        raise subprocess.CalledProcessError(
            done.returncode, ["git", *args], done.stdout, done.stderr
        )
    return done.stdout


COMMIT = "\x01goh-setting-proof "
BODY_END = "\x02"


def commits(log: str) -> Iterable[tuple[str, str, str]]:
    """(sha, message, diff) of each commit in a `git log -p` written with COMMIT/BODY_END marks."""
    for chunk in log.split(COMMIT)[1:]:
        head, _, diff = chunk.partition(BODY_END)
        sha, _, message = head.partition("\n")
        yield sha.strip(), message, diff


def judge_range(root: Path, base: str) -> tuple[list[str], int]:
    """The refusals of each commit of BASE..HEAD in ROOT, and how many were read; -1 when BASE does
    not resolve (git log refuses the range)."""
    try:
        fmt = f"--format={COMMIT}%H%n%B{BODY_END}"
        log = _git(root, "log", "--no-merges", "--reverse", "-p", "-U0", "--no-color",
                   "--no-ext-diff", fmt, f"{base}..HEAD")  # fmt: skip
    except subprocess.CalledProcessError:
        return [], -1
    out, n = [], 0
    for sha, message, diff in commits(log):
        n += 1
        out += [f"{sha[:12]}: {line}" for line in verdict(diff, message)]
    return out, n


# THE READING ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Side:
    median_s: float
    n: int
    span: str


def _when(text: str) -> float:
    return dt.datetime.fromisoformat(text).timestamp()


def window(spec: str) -> tuple[float, float]:
    """FROM..TO (ISO times, local unless they say otherwise) as epoch seconds."""
    start, sep, end = spec.partition("..")
    if not sep:
        raise ValueError(f"a window is FROM..TO (got {spec!r})")
    return _when(start), _when(end)


def side(rows: Iterable[Mapping], span: tuple[float, float], cores: int, quiet: float,
         gate_class: str | None = None) -> Side | None:  # fmt: skip
    """The median wall of ROWS that ended inside SPAN at a quiet start (load1 / CORES < QUIET),
    of GATE_CLASS alone when given; None when no row qualifies."""
    walls = [float(r["wall_s"]) for r in rows
             if span[0] <= float(r.get("epoch") or 0) <= span[1]
             and r.get("load1") is not None and float(r["load1"]) / cores < quiet
             and (gate_class is None or r.get("slot_class") == gate_class)]  # fmt: skip
    if not walls:
        return None
    start, end = (dt.datetime.fromtimestamp(t) for t in span)
    label = f"{start:%Y-%m-%dT%H:%M}..{end:%Y-%m-%dT%H:%M}"
    return Side(statistics.median(walls), len(walls), label)


def line(key: str, source: str, before: Side | None, after: Side | None, quiet: float) -> str:
    def one(name: str, s: Side | None) -> str:
        return f"{name} {s.median_s:.0f} s n={s.n} [{s.span}]" if s else f"{name}: no quiet run"

    return f"{key}: {source}: {one('before', before)}; {one('after', after)}; load/core < {quiet:g}"


def read(args: argparse.Namespace) -> int:
    import goh_db
    import long_pole
    import span_store
    import worker_share

    before, after = window(args.before), window(args.after)
    since = min(before[0], after[0]) - 1
    try:
        with goh_db.known() as con:
            lands = span_store.rows(con, since, ("land",), args.repo)
            lanes = span_store.rows(con, since, (args.lane_kind,), args.lane_repo)
    except goh_db.Unknown as e:
        err(f"{TAG} {e}")
        return 2
    cores, quiet = worker_share.cores(), long_pole.QUIET_LOAD
    got = [
        line(KEYS[0], f"kind=land repo={args.repo}", side(lands, before, cores, quiet),
             side(lands, after, cores, quiet), quiet),
        line(KEYS[1], f"kind={args.lane_kind} class=lane",
             side(lanes, before, cores, quiet, "lane"), side(lanes, after, cores, quiet, "lane"),
             quiet),
    ]  # fmt: skip
    print("\n".join(got))
    gaps = problems("\n".join(got))
    if gaps:
        warn(f"{TAG} not whole: {'; '.join(gaps)} -- {HOW}")
    return 1 if gaps else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="check_setting_proof.py")
    sub = ap.add_subparsers(dest="mode", required=True)
    st = sub.add_parser("staged")
    st.add_argument("message")
    st.add_argument("root", nargs="?", default=".")
    rg = sub.add_parser("range")
    rg.add_argument("base", nargs="?", default="main")
    rg.add_argument("root", nargs="?", default=".")
    rd = sub.add_parser("read")
    rd.add_argument("--before", required=True)
    rd.add_argument("--after", required=True)
    rd.add_argument("--repo", default="gates_of_heck")
    rd.add_argument("--lane-kind", default="suite")
    rd.add_argument("--lane-repo", default=None)
    args = ap.parse_args(argv or ["range"])  # bare: the land step's own question, main..HEAD here
    if args.mode == "read":
        return read(args)
    root = Path(args.root).resolve()
    if args.mode == "staged":
        diff = _git(root, "diff", "--cached", "-U0", "--no-color", "--no-ext-diff")
        found = verdict(diff, Path(args.message).read_text(encoding="utf-8"))
        n = 1
    else:
        # goh's tree (this file in it) is the surface's own: every entry must still match its file,
        # so an empty or moved tree is red here, never a pass over nothing (check_empty_scope.py).
        found = stale(root) if (root / "checks" / "check_setting_proof.py").is_file() else []
        got, n = judge_range(root, args.base)
        found += got
        if n < 0 and not found:
            warn(f"{TAG} not judged: {args.base} does not resolve here, so no range was read")
            return 0
    for f in found:
        err(f"{TAG} {f}")
    if found:
        return 1
    ok(
        f"{TAG} OK -- {counted(n, 'commit')} read against "
        f"{counted(len(SURFACE), 'surface entry', 'surface entries')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
