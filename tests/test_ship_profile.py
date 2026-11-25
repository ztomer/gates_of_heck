"""Where a `goh ship` spends its time, per SUB-step, with the load beside it (GOH.SHIPPROFILE).

Owner 2026-11-15: "go ship taking > 1hrs is not very productive". The result said only `release
1192 s`, `install 2037 s`: which gate check, which consumer's push, and whether the box was at load
150 when it ran were three files and a reflog away. So each step runs with GOH_TIMINGS pointed at a
file beside the result: every bounded step under it (each gate check of the release, each consumer's
pin and push of the install) lands one row with the 1-min load average at its start, and
`goh ship --status RESULT` prints them as one table, costliest first.

And the consumers are pushed CONCURRENTLY (easy win A): GOH.PINPROPAGATE made the sweep push each
consumer through its own push gate, one after another, and the install went from 86 s (v0.121.0) to
1974-2037 s (v0.122.0-v0.124.0), the sum of 12 consumer gates where the slowest alone was 756 s.
The install's commits run as many consumers at once as the machine's gate slots allow; the pushes
left the ship (GOH.PINPUSHGATE) and run one consumer gate at a time after it (GOH.LOADSOURCE: a
release's sweep was 5000-10300 CPU-s of background gates holding every long slot); the install's
rows are the commits, the sweep's the pushes.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
from pathlib import Path

import _goh_db_fixture as gdb
import push_sweep_rows
import steps_store
from conftest import REPO_ROOT, hermetic_env
from test_consumer_pins import SCRIPT, TAG, WF, _consumer, sweep_wait
from test_ship import _result, _ship, make_shipped
from timing_bounds import patience

BOUNDED = REPO_ROOT / "lib" / "bounded_run.py"
TIMINGS = REPO_ROOT / "lib" / "step_timings.py"


def _rows(path: Path) -> list[dict]:
    """The steps of the spool PATH, wherever they are now (GOH.STEPTIMES)."""
    return steps_store.rows(path) or []


def test_each_sub_step_is_recorded_with_its_load_and_status_prints_them_by_cost(repo, tmp_path):
    shipped = make_shipped(repo, tmp_path)
    py, b, t = sys.executable, BOUNDED, TIMINGS
    release = (
        f"{py} {t} time 'release phase' -- {py} {b} --timeout 30 --label 'slow check' -- sleep 0.6"
        f" && {py} {b} --timeout 30 --label 'quick check' -- true"
        ' && git tag "v$GOH_SHIP_VERSION" && git push -q origin "v$GOH_SHIP_VERSION"'
    )
    install = f"{py} {b} --timeout 30 --label 'consumer x' -- sleep 0.3"
    r = _ship(shipped, tmp_path, "--wait", GOH_SHIP_RELEASE=release, GOH_SHIP_INSTALL=install)
    assert r.returncode == 0, r.stdout + r.stderr
    res = _result(tmp_path)
    steps = {s["name"]: s for s in res["steps"]}
    rows = {x["label"]: x for x in _rows(Path(steps["release"]["timings"]))}
    for label in ("slow check", "quick check", "release phase"):
        assert isinstance(rows[label]["load1"], float), rows  # the load at the sub-step's START
    assert rows["slow check"]["parent"] == "release phase", rows  # the wrapper names its children
    assert rows["slow check"]["ms"] >= 500, rows
    inst = {x["label"]: x for x in _rows(Path(steps["install"]["timings"]))}
    assert "consumer x" in inst, inst
    left = list(tmp_path.glob("ship.*.timings.jsonl"))
    assert left == [], left  # the rows are goh-cache.db's, the files gone
    status = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "ship.py"), "--status",
                             str(tmp_path / "ship.json")], capture_output=True, text=True,
                            env=hermetic_env(drop_git=True), timeout=patience(60),
                            stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    out = status.stdout + status.stderr
    assert status.returncode == 0, out
    lines = out.splitlines()
    head = next(i for i, x in enumerate(lines) if "load" in x and "sub-step" in x)
    table = lines[head + 1 :]
    order = [next(i for i, x in enumerate(table) if label in x)
             for label in ("release phase", "slow check", "consumer x", "quick check")]  # fmt: skip
    assert order == sorted(order), out  # costliest first
    slow = table[order[1]]
    assert "release" in slow and f"{rows['slow check']['load1']:.1f}" in slow, out


HOOK = """#!/bin/sh
# a consumer's push gate: when it ran, and whether the ship's profile leaked into it
d={log}
n=$(basename "$PWD")
echo "$n start $(python3 -c 'import time; print(time.time())')" >>"$d"
[ -z "${{GOH_TIMINGS:-}}" ] || echo "$n leaked $GOH_TIMINGS" >>"$d"
sleep 0.5
echo "$n end $(python3 -c 'import time; print(time.time())')" >>"$d"
"""


def test_the_sweep_pushes_its_consumers_one_after_another_each_profiled(tmp_path):
    log = tmp_path / "gates.log"
    names = ("one", "two", "three")
    for n in names:
        flow = WF.format(tag="v0.96.0", ruff="0.0.1")
        repo = _consumer(tmp_path, n, {".github/workflows/ci.yml": flow})
        hook = repo / ".git" / "hooks" / "pre-push"
        hook.write_text(HOOK.format(log=log))
        hook.chmod(0o755)
    timings = tmp_path / "ship.install.timings.jsonl"
    env = hermetic_env(drop_git=True, GOH_ESTATE_STATE=str(tmp_path / "state" / "estate.json"),
                       GOH_SHIP_RESULT=str(tmp_path / "ship.json"), GOH_GATE_SLOTS="3",
                       GOH_TIMINGS=str(timings))  # fmt: skip
    r = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path / "estate"), "--slug",
                        "owner/gates_of_heck", "--tag", TAG, "--bump", *sweep_wait()],
                       capture_output=True,
                       text=True, check=False, env=env, timeout=patience(120),
                       stdin=subprocess.DEVNULL)  # fmt: skip
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    rec = gdb.pinned(tmp_path / "ship.json")
    assert {k: v["status"] for k, v in rec["repos"].items()} == dict.fromkeys(names, "committed")
    rec = push_sweep_rows.read(rec["sweep"])
    assert {k: v["status"] for k, v in rec["repos"].items()} == dict.fromkeys(names, "pushed"), rec
    said = log.read_text()
    assert "leaked" not in said, said  # a consumer's gate is its own, never the ship's profile
    spans: dict[str, dict[str, float]] = {}
    for ln in said.splitlines():
        n, what, when = ln.split()
        spans.setdefault(n, {})[what] = float(when)
    assert set(spans) == set(names), said
    # one gate at a time, whatever the slots (GOH.LOADSOURCE): each ended before the next started
    order = sorted(spans.values(), key=lambda s: s["start"])
    assert all(a["end"] <= b["start"] for a, b in itertools.pairwise(order)), spans
    rows = {x["label"]: x for x in _rows(timings)}  # the install's: each consumer's commit
    pushes = {x["label"]: x for x in _rows(tmp_path / "ship.sweep.timings.jsonl")}
    for n in names:
        row = rows[f"commit {n}"]
        assert row["tier"] == "consumer" and isinstance(row["load1"], float), rows
        row = pushes[f"push {n}"]
        assert row["tier"] == "consumer" and isinstance(row["load1"], float), pushes
        assert row["ms"] >= 450, row  # the push through its gate, in the sweep


def test_status_names_the_seconds_no_row_recorded(tmp_path):
    """GOH.RELEASELANDGATE: v0.139.4's table showed its land at 160 s and nothing said that its rows
    named 6 s of it; the status says so under the table (lib/step_attribution.py)."""
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    import ship_profile

    def res(rows: list[tuple[str, float, str]]) -> dict:
        f = tmp_path / f"t{len(rows)}.jsonl"
        f.write_text("".join(json.dumps({"label": a, "ms": s * 1000, "rc": 0, "tier": "phase",
                                         "parent": p}) + "\n" for a, s, p in rows))  # fmt: skip
        return {"steps": [{"name": "release", "timings": str(f)}]}

    land = "release: land (its release gate)"
    gap = ship_profile.unattributed(res([("ship release", 650, ""), (land, 160, "ship release"),
                                         ("release: push main", 485, "ship release"),
                                         ("a check", 6, land)]))  # fmt: skip
    assert len(gap) == 1 and "in release" in gap[0] and land in gap[0], gap
    assert "154.0 s is unattributed" in gap[0], gap
    named = res([("ship release", 50, ""), (land, 20, "ship release"), ("land: gate", 19, land),
                 ("release: push main", 29, "ship release")])  # fmt: skip
    assert ship_profile.unattributed(named) == []
