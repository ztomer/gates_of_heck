"""lib/land_steps.py -- the readers of a land's logs and gate timings, for `goh land-profile`.

Split from lib/land_profile.py (GOH.LANDPROFILEFIX: its 450-line headroom): a land's log files
(its record's, its own, its batch gate's), the gate timings kept beside them, a step table's rows
and the coverage step's phases. Pinned by tests/test_land_profile.py and test_land_residue.py.

The readers of a land's logs and gate timings for `goh land-profile` (split from `land_profile.py`,
GOH.LANDPROFILEFIX): the land's log files (its record's, its own, its batch gate's), the timings
kept beside them, a step table's rows, the coverage phases. Pinned by `test_land_profile.py`.

`lib/land_prestep.py`: The land pre-step run at ENQUEUE (GOH.ENQUEUEPRE): `goh land --enqueue
--land` runs `tools/land.sh` with `GOH_LAND_PRE_ONLY=1` in the request's slot (`lib/land_slot.py`),
so the lander's own set, short lane and deadline decide before a tip joins the serial queue; a red
set refuses the tip naming the failing tests, a deadline is no verdict and queues.
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

RUN_LOG = re.compile(r"^batch \S+: \w+ by the gate run (\S+\.log)$", re.MULTILINE)
TIMINGS = re.compile(r"^timings: (\S+)\s*$", re.MULTILINE)
TOP_STEP = re.compile(r"^→ \[\d+/\d+\] \(\.gatesrc\) (.+?)\s*$", re.MULTILINE)
NUM = r"(\d+(?:\.\d+)?)"
TABLE_ROW = re.compile(rf"^\s*{NUM} s(?:\s+{NUM} s cpu)?\s+rc=\d+\s+(\S+)\s+(.+?)\s*$")
PHASE = re.compile(r"✓ phase '(.+)': (\d+) s")
COVERAGE_LABEL = re.compile(r"^coverage \(floor [^)]*\)")


def label(raw: str) -> str:
    """A step's name without its run-varying parts (the coverage floor, the plain command)."""
    raw = COVERAGE_LABEL.sub("coverage", raw.strip()).removeprefix('"$GOH_PY" ').removeprefix("./")
    return raw.partition(": ")[0] if raw.startswith("external (") else raw


def logs_of(q: Path, path: Path, rec: dict) -> list[Path]:
    """The land's log files: its record's, its own, its batch gate's -- the gate run its own log
    names (`... by the gate run NAME`, lib/landq_batch.py own_log) when another item's batch
    landed it, so the second item of a batch reads the gate that landed it too."""
    logs = q / "logs"
    found = [Path(rec["log"])] if rec.get("log") else []
    found += [logs / f"{path.stem}.log", *sorted(logs.glob(f"{path.stem}.gate*.log"))]
    for log in list(found):
        with contextlib.suppress(OSError):
            found += [logs / n for n in RUN_LOG.findall(log.read_text(errors="replace"))]
    return list(dict.fromkeys(found))


def kept_timings(q: Path, path: Path, rec: dict) -> list[Path]:
    """The gate timings the lander kept beside each of the land's logs (`<stem>.timings.jsonl`,
    lib/land_prestep.py timings_keep, GOH.LANDLEAKS), the record's log's first."""
    return [log.with_name(log.name.removesuffix(".log") + ".timings.jsonl")
            for log in logs_of(q, path, rec)]  # fmt: skip


def texts(q: Path, path: Path, rec: dict) -> list[str]:
    """The land's logs' text (`logs_of`)."""
    out = []
    for log in logs_of(q, path, rec):
        try:
            out.append(log.read_text(errors="replace"))
        except OSError:
            continue
    return out


def steps_from_timings(path: Path) -> list[dict] | None:
    """Each gate step of a timings file, summed by (parent, label); None when the file is gone."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    acc: dict[tuple[str, str], dict] = {}
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict) or r.get("tier") != "step":
            continue
        key = (label(str(r.get("parent") or "")), label(str(r.get("label") or "")))
        s = acc.setdefault(key, {"parent": key[0], "label": key[1], "wall": 0.0, "cpu": None})
        s["wall"] += float(r.get("ms") or 0) / 1000
        if r.get("cpu_ms") is not None:
            s["cpu"] = (s["cpu"] or 0.0) + float(r["cpu_ms"]) / 1000
    return list(acc.values())


def steps_from_log(text: str) -> list[dict]:
    """The gate steps of a land log's LAST step table (the timings file is gone); a row that is not
    one of the gate's own steps is nested, its parent unknown (`?`)."""
    tops = {label(t) for t in TOP_STEP.findall(text)}
    blocks = text.split("-- top 20 steps by wall time --")
    if len(blocks) < 2:
        return []
    acc: dict[str, dict] = {}
    for line in blocks[-1].splitlines()[1:]:
        if line.startswith("--"):
            break
        m = TABLE_ROW.match(line)
        if not m or m.group(3) == "tree":
            continue
        name = label(m.group(4))
        s = acc.setdefault(name, {"parent": "" if name in tops else "?", "label": name,
                                  "wall": 0.0, "cpu": None})  # fmt: skip
        s["wall"] += float(m.group(1))
        if m.group(2):
            s["cpu"] = (s["cpu"] or 0.0) + float(m.group(2))
    return list(acc.values())


def gate_steps(found: list[str], kept: list[Path] | tuple = ()) -> list[dict]:
    """The gate's steps: from its timings kept in the queue's logs, else the one a log names when
    it is left (lands before GOH.LANDLEAKS), else from its log's table."""
    for p in kept:
        got = steps_from_timings(p)
        if got is not None:
            return got
    for text in found:
        for p in dict.fromkeys(TIMINGS.findall(text)):
            got = steps_from_timings(Path(p))
            if got is not None:
                return got
    for text in found:
        got = steps_from_log(text)
        if got:
            return got
    return []


def phases(found: list[str]) -> dict[str, float]:
    """The coverage step's phases, from the land's logs (lib/log_follow.py carries them there) and
    its gate's own `<timings>.log` where an older land left one."""
    out: dict[str, float] = {}
    seen = list(found)
    for text in found:
        for p in dict.fromkeys(TIMINGS.findall(text)):
            try:
                seen.append(Path(p + ".log").read_text(errors="replace"))
            except OSError:
                continue
    for text in seen:
        for name, secs in PHASE.findall(text):
            out[name] = out.get(name, 0.0) + float(secs)
    return out
