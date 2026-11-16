"""Where a `goh ship` spends its time, per SUB-step, with the load beside it (GOH.SHIPPROFILE).

Owner 2026-11-15: "go ship taking > 1hrs is not very productive". The result said only `release
1192 s`, `install 2037 s`: which gate check, which consumer's push, and whether the box was at load
150 when it ran were three files and a reflog away. So each step runs with GOH_TIMINGS pointed at a
file beside the result: every bounded step under it (each gate check of the release, each consumer's
pin and push of the install) lands one row with the 1-min load average at its start, and
`goh ship --status RESULT` prints them as one table, costliest first.

And the install's consumers are pushed CONCURRENTLY (easy win A): GOH.PINPROPAGATE made the sweep
push each consumer through its own push gate, one after another, and the install went from 86 s
(v0.121.0) to 1974-2037 s (v0.122.0-v0.124.0), the sum of 12 consumer gates where the slowest alone
was 756 s. The sweep now runs as many consumers at once as the machine's gate slots allow.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env
from test_consumer_pins import SCRIPT, TAG, WF, _consumer
from test_ship import _result, _ship, make_shipped
from timing_bounds import patience

BOUNDED = REPO_ROOT / "lib" / "bounded_run.py"
TIMINGS = REPO_ROOT / "lib" / "step_timings.py"


def _rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


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
    status = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "ship.py"), "--status",
                             str(tmp_path / "ship.json")], capture_output=True, text=True,
                            env=hermetic_env(drop_git=True), timeout=60,
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
sleep 1.5
echo "$n end $(python3 -c 'import time; print(time.time())')" >>"$d"
"""


def test_the_install_pushes_its_consumers_concurrently_each_profiled(tmp_path):
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
                        "owner/gates_of_heck", "--tag", TAG, "--bump"], capture_output=True,
                       text=True, check=False, env=env, timeout=patience(120),
                       stdin=subprocess.DEVNULL)  # fmt: skip
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    rec = json.loads((tmp_path / "ship.pins.json").read_text())
    assert {k: v["status"] for k, v in rec["repos"].items()} == dict.fromkeys(names, "pushed"), rec
    said = log.read_text()
    assert "leaked" not in said, said  # a consumer's gate is its own, never the ship's profile
    spans: dict[str, dict[str, float]] = {}
    for ln in said.splitlines():
        n, what, when = ln.split()
        spans.setdefault(n, {})[what] = float(when)
    assert set(spans) == set(names), said
    # every gate started before any other ended: they ran at once, not one after another
    assert max(s["start"] for s in spans.values()) < min(s["end"] for s in spans.values()), spans
    rows = {x["label"]: x for x in _rows(timings)}
    for n in names:
        row = rows[f"consumer {n}"]
        assert row["tier"] == "consumer" and isinstance(row["load1"], float), rows
        assert row["ms"] >= 1400, row  # the commit AND its push, not the listing
