"""THE LAND PATH'S SHARE AND THE LANES' CLAMP (GOH.LANDSHARE): who gets the cores, and at what QoS.

lib/worker_share.py sizes a gate's width; this module decides how the cores are SPLIT between the
land path and everything else, and which gates run under a CPU clamp. Imported by worker_share.py
(`live`, the admission's share), lib/admission.py and tools/load.py (`qos_prefix`). Pinned by
tests/test_land_share.py.

THE MEASUREMENT (2026-11-21). Every admitted gate got `cores // admitted`, so a land's enqueue
pre-step and the lander's pre-land ran at 5 of 16 workers beside lane and other-repo gates. One land
by stage (GOH.UGREPVQ): the pre-step took 1314 s -- 345 s waiting for a short slot, then 969 s at 5
workers -- and the lander ran the same set again at 5 workers: 39 min enqueue to landed, for tests
that cost 850-3200 serial s (1-4 min at the machine's width). The land path is the critical path
of every lane's work; lane and work gates are not.

THE WEIGHTED SHARE. While a land-path run is admitted -- the enqueue pre-step and the lander's
pre-land (class `pre`), a land's gate (`land`), `goh ship` (`ship`) -- the land path takes
`GOH_LAND_SHARE` of the cores (3/4 unless the env or the repo's .gatesrc names another fraction),
split equally among the land-path runs; every other gate (lane, work, a commit gate) splits the
rest, at least 1 each. With no land-path run admitted the rule is worker_share's old one,
`cores // admitted`. THE BOUND is kept: the land part is at least one per land run and leaves at
least one per other gate, so the shares sum to at most the cores whenever the gates fit at one
core each (beyond that every gate gets 1, as `divide` always did).

THE CLAMP (QoS). A lane- or work-class gate runs under a CPU clamp; the land path, a commit gate
and a ship never do. Probed 2026-11-21 at load 48-50 on 12P+4E cores, fixed work x8: default
1.9-5.1 s; `nice 19` 3.0-4.3 s (no effect on macOS); `taskpolicy -c utility` 11.3-34.2 s;
background 37-60 s. So on macOS the lever is the utility QoS clamp, set at EXEC (taskpolicy's `-c`
does not apply to a running pid). On Linux it is nice + ionice's lowest best-effort level (a
cgroup weight needs a delegated cgroup the gate does not own: not used). An OS outside the house
targets is a hard refusal behind the `GOH_QOS_OS` seam; a missing tool degrades with its reason.
`GOH_LANE_QOS=off` turns the clamp off (a timing probe that must run at default QoS).

A GATE THAT RUNS NO SUITE (GOH.PREWIDTH). A land's gate whose suite carries the pre-step's verdict
(tools/pytest.sh) runs only its structural, cargo and canary steps, yet it held an equal share: two
pre-steps beside it got 16 // 3 = 5 workers each. It now says so (`declare`, a word beside its slot,
`<slots>/widths/<slot>`, honoured only while the same owner holds the slot) and every share counts
it at its REAL width -- the busy cores of the suite-carried land rows in the timing ledger, median
cpu_s / wall_s (`real_width`; 2.4-4.3 measured 2026-11-21, SUITELESS_SEED before any) -- taken
off the cores before the rest are split. THE BOUND holds with it: the shares plus the real widths
sum to at most the cores.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import os
import shutil
import sys
from collections.abc import Callable

LAND_PATH = frozenset({"pre", "land", "ship"})  # the runs a land waits on (lib/gate_lanes.py)
CLAMPED = frozenset({"lane", "work"})  # the classes run under the CPU clamp
SHARE_ENV = "GOH_LAND_SHARE"  # the knob: the land path's fraction of the cores, (0, 1]
SHARE_DEFAULT = (
    0.75  # the plan's start (rules.spd_plan.strategy_20261121): 12 of 16 = the suite's cap
)
RESOLVED_ENV = "GOH_SHARE_LAND"  # the fraction as the admission resolved it, for every runner
CLASS_ENV = "GOH_SHARE_CLASS"  # the class this run's share is counted as (set at admission)
QOS_ENV = "GOH_LANE_QOS"  # `off` turns the clamp off; anything else (or unset) leaves it on
OS_ENV = "GOH_QOS_OS"  # the seam: darwin | linux (tests pin both on any box)
DEFAULT_CLASS = "work"
WIDTHS = "widths"  # <slots>/widths/<slot name>: `<owner pid>\t<real width>` of a gate with no suite
SUITELESS_SEED = 3  # the cores a suite-carried land gate kept busy, before the ledger has a row
WIDTH_WINDOW = 20  # the newest suite-carried land rows the real width is measured over


def fraction(
    environ: dict[str, str] | None = None, gatesrc: Callable[[str], str] | None = None
) -> float:
    """The land path's fraction: GOH_SHARE_LAND (resolved at admission), else GOH_LAND_SHARE from
    the environment, else from `gatesrc` (the repo's .gatesrc, read only where a gate is admitted),
    else 3/4. ValueError for anything but a number in (0, 1]."""
    env = os.environ if environ is None else environ
    raw = env.get(RESOLVED_ENV, "").strip() or env.get(SHARE_ENV, "").strip()
    if not raw and gatesrc is not None:
        raw = gatesrc(SHARE_ENV).strip()
    if not raw:
        return SHARE_DEFAULT
    try:
        got = float(raw)
    except ValueError:
        got = -1.0
    if not 0.0 < got <= 1.0:
        raise ValueError(f"{SHARE_ENV} must be a fraction of the cores in (0, 1] (got {raw!r})")
    return got


def weighted(
    total: int, classes: list[str], mine: str, frac: float = SHARE_DEFAULT, fixed: int = 0
) -> int:
    """`mine`'s workers among the admitted gates of `classes` (mine among them) on `total` cores,
    less `fixed`: the real widths of the gates that run no suite (GOH.PREWIDTH)."""
    total = max(len(classes), total - max(0, fixed), 1)
    gates = max(1, len(classes))
    land = sum(1 for c in classes if c in LAND_PATH)
    if mine in LAND_PATH:
        land = max(1, land)
    if land == 0:
        return max(1, total // gates)
    rest = max(0, gates - land)
    if land + rest > total:  # more gates than cores: one each, as `divide` always gave
        return 1
    part = max(land, min(total - rest, round(total * frac))) if rest else total
    if mine in LAND_PATH:
        return max(1, part // land)
    return max(1, (total - part) // rest)


def runner_vars(gate_class: str, environ: dict[str, str]) -> dict[str, str]:
    """What an admission hands every runner (lib/worker_share.py `runner_env`): the class its live
    share counts it as and the land fraction it resolved; {} with no class (not an admission)."""
    if not gate_class:
        return {}
    return {CLASS_ENV: gate_class, RESOLVED_ENV: repr(fraction(environ, gatesrc))}


def held_classes(directory: str) -> list[str]:
    """The class of every gate holding a slot in `directory` now (its record's fifth field)."""
    return [klass for klass, _ in held_entries(directory)]


def held_entries(directory: str) -> list[tuple[str, int | None]]:
    """(class, real width or None) of every gate holding a slot in `directory` now: a width only
    for a gate that declared it runs no suite (`declare`)."""
    import gate_slot

    return [(record_class(p), declared_width(p)) for p in gate_slot.slot_files(directory)
            if gate_slot.held(p)]  # fmt: skip


def _owner(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().split("\t", 1)[0].strip()
    except OSError:
        return ""


def declared_width(slot: str) -> int | None:
    """The real width the holder of SLOT declared, or None: no word, or one a previous holder left
    (its owner pid is not the record's)."""
    try:
        with open(os.path.join(os.path.dirname(slot), WIDTHS, os.path.basename(slot)),
                  encoding="utf-8") as f:  # fmt: skip
            owner, width = f.read().split()
    except (OSError, ValueError):
        return None
    if not width.isdigit() or owner != _owner(slot):
        return None
    return int(width)


def declare(width: int, environ: dict[str, str] | None = None) -> bool:
    """Say the gate holding GOH_GATE_SLOT (`<slot file> <owner pid>`) runs no suite and keeps WIDTH
    cores busy; False, writing nothing, outside a held slot."""
    env = os.environ if environ is None else environ
    claim = env.get("GOH_GATE_SLOT", "").rsplit(" ", 1)
    if len(claim) != 2 or not claim[1].isdigit() or _owner(claim[0]) != claim[1]:
        return False
    where = os.path.join(os.path.dirname(claim[0]), WIDTHS)
    try:
        os.makedirs(where, exist_ok=True)
        tmp = os.path.join(where, f".{os.path.basename(claim[0])}.{os.getpid()}")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(f"{claim[1]}\t{max(0, int(width))}\n")
        os.replace(tmp, os.path.join(where, os.path.basename(claim[0])))
    except OSError:
        return False
    return True


def real_width(rows: list[dict]) -> int:
    """The cores a suite-carried land gate keeps busy: the median cpu_s / wall_s of the newest
    WIDTH_WINDOW land rows that carried their suite, rounded, at least 1; SUITELESS_SEED with
    none."""
    rates = sorted([
        r["cpu_s"] / r["wall_s"] for r in rows
        if r.get("kind") == "land" and r.get("suite_reused")
        and isinstance(r.get("cpu_s"), (int, float)) and isinstance(r.get("wall_s"), (int, float))
        and r["wall_s"] > 0
    ][-WIDTH_WINDOW:])  # fmt: skip
    if not rates:
        return SUITELESS_SEED
    mid = len(rates) // 2
    median = rates[mid] if len(rates) % 2 else (rates[mid - 1] + rates[mid]) / 2
    return max(1, round(median))


def share_of(
    directory: str, environ: dict[str, str], total: int, extra: int, mine: str | None = None
) -> int:
    """`mine`'s workers on `total` cores among the held slots' classes plus `extra` (1: this run
    holds no slot and is one more of class `mine`). `mine` unnamed: GOH_SHARE_CLASS, else work."""
    mine = mine or environ.get(CLASS_ENV, "").strip() or DEFAULT_CLASS
    entries = held_entries(directory)
    classes = [klass for klass, width in entries if width is None] + [mine] * extra
    fixed = sum(width for _, width in entries if width is not None)
    return weighted(total, classes, mine, fraction(environ), fixed)


def gatesrc(key: str) -> str:
    """KEY as the current repository's .gatesrc sets it (lib/gate_lanes.py's reader), else ""."""
    import gate_lanes

    return gate_lanes._gatesrc(key)


def record_class(path: str) -> str:
    """The class a held slot was taken by: the record's fifth field (lib/admission.py `admit`);
    a record from before it, or none yet, is `short` in the short lane and `work` in the long."""
    try:
        with open(path, encoding="utf-8") as f:
            fields = f.read().strip().split("\t")
    except OSError:
        fields = []
    if len(fields) == 5 and fields[4]:
        return fields[4]
    return "short" if os.path.basename(path).startswith("short-") else DEFAULT_CLASS


def qos_prefix(
    gate_class: str,
    environ: dict[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[list[str], str]:
    """(argv prefix, why) for a gate of `gate_class`: the clamp a lane/work gate execs under, or []
    with the reason it runs at default QoS. SystemExit naming the seam on an OS outside the house
    targets (darwin, linux)."""
    env = os.environ if environ is None else environ
    if gate_class not in CLAMPED:
        return (
            [],
            f"class {gate_class}: default QoS (the land path and commit gates are never clamped)",
        )
    if env.get(QOS_ENV, "").strip() == "off":
        return [], f"class {gate_class}: default QoS ({QOS_ENV}=off)"
    system = env.get(OS_ENV, "").strip() or sys.platform
    if system == "darwin":
        if which("taskpolicy") is None:
            return [], f"class {gate_class}: default QoS -- taskpolicy not found on PATH, no clamp"
        return ["taskpolicy", "-c", "utility"], f"class {gate_class}: taskpolicy -c utility"
    if system.startswith("linux"):
        if which("nice") is None:
            return [], f"class {gate_class}: default QoS -- nice not found on PATH, no clamp"
        argv = ["nice", "-n", "10"]
        if which("ionice") is None:
            return argv, f"class {gate_class}: nice -n 10 (ionice not found: CPU only)"
        return [
            *argv,
            "ionice",
            "-c",
            "2",
            "-n",
            "7",
        ], f"class {gate_class}: nice -n 10 + ionice -c 2 -n 7"
    raise SystemExit(
        f"gate QoS: unsupported OS {system!r} -- the house targets are darwin and linux "
        f"({OS_ENV} is the seam)"
    )


def clamp(
    command: list[str], say: Callable[[str], None], environ: dict[str, str] | None = None
) -> list[str]:
    """tools/load.py's gate run (N=0): `command` under its admission's class's clamp; the land path,
    and a run whose admission named no class (slots off), unchanged. Never an experiment's
    command: N spinners at default QoS starve a clamped command by construction
    (lib/load_priority.py, G1f) -- why load.py has --background-ok. SystemExit on an unsupported
    OS."""
    env = os.environ if environ is None else environ
    gate_class = env.get(CLASS_ENV, "").strip()
    if not gate_class:
        return command
    prefix, why = qos_prefix(gate_class, env)
    if gate_class in CLAMPED:
        say(
            f"load: QoS {why} (GOH.LANDSHARE): a timing-sensitive test under the clamp may need "
            "wider deadlines when the machine is contended"
        )
    return [*prefix, *command]


def main(argv: list[str]) -> int:
    """`qos --class C`: why, then the clamp's argv a word a line (lib/gate_slot.sh reads it into
    GATE_SLOT_QOS); exit 2, naming the seam, on an OS outside the house targets."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("qos", help="the clamp a gate of CLASS execs under")
    q.add_argument("--class", dest="gate_class", required=True)
    sub.add_parser("suiteless", help="this gate runs no suite: count it at its real width")
    args = ap.parse_args(argv)
    if args.cmd == "suiteless":  # tools/pytest.sh, its suite carried (GOH.PREWIDTH)
        import timing_ledger

        width = real_width(timing_ledger.read())
        if declare(width):
            print(f"share: this gate runs no suite -- counted at its real width, {width} cores "
                  "(GOH.PREWIDTH, the suite-carried land rows' median)")  # fmt: skip
        return 0  # an instrument: never the reason a gate fails
    try:
        argv_prefix, why = qos_prefix(args.gate_class)
    except SystemExit as refusal:
        print(refusal, file=sys.stderr)
        return 2
    print(why)  # the first line: why; then the argv, a word a line
    for word in argv_prefix:
        print(word)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
