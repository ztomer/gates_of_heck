"""spawn_ledger.py -- reading tools/spawn_profile.py's ledger: kinds, callers, cost, blind share.

One row per process a shim saw (FIELDS). Split from spawn_profile.py, which runs and calibrates, so
each stays under the file-length cap. Pinned by tests/test_spawn_profile.py.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

FIELDS = ("kind", "shim_pid", "pid", "ppid", "t0", "wall", "user", "sys", "status", "argv")


def classify(comm: str) -> str:
    """A process's kind from its command name or path (`ps -o comm`, an argv[0])."""
    base = os.path.basename(comm.strip())
    if re.match(r"(?i)^python[\d.]*$", base):
        return "python"
    if base in ("bash", "sh", "zsh", "dash"):
        return "bash"
    if base == "git" or base.startswith("git-"):
        return "git"
    if base == "goh":
        return "goh"
    if base == "cargo":
        return "cargo"
    if base in ("rustc", "cargo-clippy", "clippy-driver", "rustdoc", "cargo-llvm-cov"):
        return "rustc"  # cargo's own children: no shim, by design
    return "other"


SHIMMED = ("python", "bash", "git", "goh", "cargo")


def read_ledger(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t", len(FIELDS) - 1)
        if len(parts) != len(FIELDS):
            continue
        row = dict(zip(FIELDS, parts, strict=True))
        for key in ("shim_pid", "pid", "ppid", "status"):
            row[key] = int(row[key])
        for key in ("t0", "wall", "user", "sys"):
            row[key] = float(row[key])
        row["cpu"] = row["user"] + row["sys"]
        rows.append(row)
    return rows


GIT_VALUED = ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path")


def script_of(row: dict) -> str:
    """The script a python/bash process runs (`tools/x.py`, `gates/y.sh`, `-m pytest`, `bash -c`),
    or a git/cargo/goh process's verb (`git rev-parse`): the first word that is not an option."""
    words = row["argv"].split()
    kind = row["kind"]
    if kind in ("python3", "python", "bash", "sh"):
        for i, word in enumerate(words):
            if word == "-c":
                return f"{kind} -c"
            if word == "-m":
                return "-m " + (words[i + 1] if i + 1 < len(words) else "?")
            if word.startswith("-"):
                continue
            return "/".join(Path(word).parts[-2:]) if "/" in word else word
        return f"{kind} (stdin)"
    skip = False
    for word in words:
        if skip:
            skip = False
            continue
        if word in GIT_VALUED:
            skip = True
            continue
        if word.startswith(("-", "+")) or "=" in word:
            continue
        return f"{kind} {word}"
    return kind


def caller_of(row: dict, by_pid: dict[int, dict]) -> str:
    """The nearest ancestor in the ledger that runs a named script: the script that started this."""
    hops = 0
    parent = by_pid.get(row["ppid"])
    first = script_of(parent) if parent is not None else ""
    while parent is not None and hops < 64:
        name = script_of(parent)
        if parent["kind"] in ("python3", "python", "bash", "sh") and not name.endswith(
            ("-c", "(stdin)")
        ):
            return name
        parent = by_pid.get(parent["ppid"])
        hops += 1
    return "(root)" if hops == 0 else f"({first})"


def summarize(
    out: Path, costs: dict[str, tuple[float, float]] | None = None, top: int = 20
) -> dict:
    """Counts per kind and per (caller, callee), priced by `costs` (kind -> (wall s, cpu s))."""
    rows = read_ledger(out / "ledger.tsv")
    meta = (
        json.loads((out / "run.json").read_text(encoding="utf-8"))
        if (out / "run.json").exists()
        else {}
    )
    by_pid = {r["pid"]: r for r in rows}
    child_cpu: Counter = Counter()
    for r in rows:
        if r["ppid"] in by_pid:
            child_cpu[r["ppid"]] += r["cpu"]
    for r in rows:
        r["self_cpu"] = max(0.0, r["cpu"] - child_cpu[r["pid"]])
        r["family"] = classify(r["kind"])
    costs = costs or {}
    kinds = Counter(r["family"] for r in rows)
    start_cost = {k: costs.get(k, (0.0, 0.0)) for k in kinds}
    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (caller_of(r, by_pid), script_of(r))
        g = groups.setdefault(
            key,
            {
                "caller": key[0],
                "callee": key[1],
                "kind": r["family"],
                "n": 0,
                "self_cpu": 0.0,
                "wall": 0.0,
            },
        )
        g["n"] += 1
        g["self_cpu"] += r["self_cpu"]
        g["wall"] += r["wall"]
    for g in groups.values():
        g["start_wall_s"] = round(g["n"] * start_cost[g["kind"]][0], 3)
        g["start_cpu_s"] = round(g["n"] * start_cost[g["kind"]][1], 3)
        g["self_cpu"] = round(g["self_cpu"], 3)
        g["wall"] = round(g["wall"], 3)
    ranked = sorted(groups.values(), key=lambda g: (g["start_cpu_s"], g["n"]), reverse=True)
    start_wall = sum(kinds[k] * start_cost[k][0] for k in kinds)
    start_cpu = sum(kinds[k] * start_cost[k][1] for k in kinds)
    blind = blind_share(out, rows)
    return {
        "rows": len(rows),
        "by_kind": dict(kinds.most_common()),
        "start_cost_s": start_cost,
        "start_wall_s": round(start_wall, 3),
        "start_cpu_s": round(start_cpu, 3),
        "run": {k: meta.get(k) for k in ("wall", "cpu", "load_start", "load_end", "rc")},
        "start_share_cpu": round(start_cpu / meta["cpu"], 4) if meta.get("cpu") else None,
        # summed per-process start wall over the run's wall: processes overlap, so this can pass 1
        "start_wall_per_run_wall": round(start_wall / meta["wall"], 4)
        if meta.get("wall")
        else None,
        "top": ranked[:top],
        "blind": blind,
    }


def blind_share(out: Path, rows: list[dict]) -> dict:
    """Sampled descendants the ledger never recorded, by kind; their share of sampled time."""
    samples = out / "samples.tsv"
    if not samples.exists():
        return {"sampled": False}
    known = {r["pid"] for r in rows} | {r["shim_pid"] for r in rows}
    seen: dict[int, list] = {}
    for line in samples.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[1].isdigit():
            ent = seen.setdefault(int(parts[1]), [parts[2], 0])
            ent[1] += 1
    weight: Counter = Counter()
    blind_w: Counter = Counter()
    blind_n: Counter = Counter()
    pids_n: Counter = Counter()
    blind_comm: Counter = Counter()
    unshimmed: Counter = Counter()
    for pid, (comm, n) in seen.items():
        kind = classify(comm)
        if kind not in SHIMMED:  # a kind with no shim is not "blind", it was never aimed at
            unshimmed[os.path.basename(comm)] += n
            continue
        weight[kind] += n
        pids_n[kind] += 1
        if pid not in known:
            blind_w[kind] += n
            blind_n[kind] += 1
            blind_comm[os.path.basename(comm)] += n
    total = sum(weight.values())
    return {
        "sampled": True,
        "pids": sum(pids_n.values()),
        "blind_pids": sum(blind_n.values()),
        "blind_time_share": round(sum(blind_w.values()) / total, 4) if total else None,
        "by_kind": {
            k: {
                "sampled_pids": pids_n[k],
                "blind_pids": blind_n[k],
                "time_share": round(blind_w[k] / weight[k], 4),
            }
            for k in weight
        },
        "unshimmed_comm": unshimmed.most_common(8),
        "top_blind_comm": blind_comm.most_common(8),
    }
