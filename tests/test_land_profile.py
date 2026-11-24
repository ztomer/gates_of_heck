"""`goh land-profile` splits a land end to end into stages (lib/land_profile.py, GOH.LANDPROFILE).

A fixture landing queue holds two lands with known stages: an instrumented one that was red once
(its timings file and coverage phases still on disk) and a plain one whose timings file is gone (its
log's step table is all that is left). The report must give back exactly the stages put in.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import land_profile
import land_records

T0 = dt.datetime(2026, 11, 18, 15, 0, tzinfo=dt.UTC)
REPO = "/p/gates_of_heck"


def at(minutes: float) -> dt.datetime:
    return T0 + dt.timedelta(minutes=minutes)


def put(q: Path, filed: float, item: str, state: str, started: float, finished: float,
        log: str = "", **extra: object) -> str:  # fmt: skip
    for d in ("logs",):
        (q / d).mkdir(parents=True, exist_ok=True)
    stem = f"{int(at(filed).timestamp() * 1e9):020d}-{item}"
    rec = {"repo": REPO, "mode": "land", "state": state, "item": item,
           "started": at(started).isoformat(), "finished": at(finished).isoformat(),
           "log": str(q / "logs" / f"{stem}.log"), **extra}  # fmt: skip
    land_records.write(q, f"{stem}", rec)
    (q / "logs" / f"{stem}.log").write_text(log)
    return stem


def step(label: str, s: float, cpu: float | None = None, parent: str = "") -> str:
    row = {"label": label, "ms": s * 1000, "rc": 0, "tier": "step", "parent": parent}
    return json.dumps(row | ({} if cpu is None else {"cpu_ms": cpu * 1000}))


@pytest.fixture
def queue(tmp_path: Path) -> tuple[Path, list[dict]]:
    """INST: requested 0, filed 8 (pre-step 480 s), red 10-12, again 15-30 (pre-land 120 s, gate
    780 s of which slot 60 s, rust_gate 600 s > coverage 550 s, canary 100 s, cached second pass).
    PLAIN: filed 40, run 45-55 (pre-land 60 s), its timings gone: the log's last table."""
    q = tmp_path / "q"
    timings = tmp_path / "goh-land-timings.X"
    timings.write_text("\n".join([
        step("./gates/rust_gate.sh", 600, 3000),
        step("coverage (floor 93.1%)", 550, 2900, "./gates/rust_gate.sh"),
        json.dumps({"label": "no emoji", "ms": 50, "tier": "native", "parent": "coverage"}),
        step('"$GOH_PY" ./scripts/release_canary.py', 100, 400),
        step("./gates/rust_gate.sh", 0) + "\nnot json",
    ]))  # fmt: skip
    (tmp_path / "goh-land-timings.X.log").write_text(
        "  ✓ phase 'workspace: instrumented build': 40 s\n"
        "  ✓ phase 'external: GOH_TEST_BIN=x ./tools/pytest.sh': 500 s\n"
    )
    put(q, 8, "GOH.INST", "red", 10, 12, requested=at(0).isoformat())
    put(q, 13, "GOH.INST", "landed", 15, 30, slot_wait_s=60.0, load_start=12.0,
        log="✓ pre-land green in 120 s: waited 0 s\n"
            "  550.00 s 2900.00 s cpu  rc=0   wt   coverage (floor 93.1%)\n"
            f"timings: {timings}\n")  # fmt: skip
    plain_log = (
        "✓ pre-land green in 60 s\n→ [1/2] (.gatesrc) ./gates/rust_gate.sh\n"
        '→ [2/2] (.gatesrc) "$GOH_PY" ./scripts/release_canary.py\n'
        "-- top 20 steps by wall time --\n  1.00 s 1.00 s cpu  rc=0   wt   stale\n"
        "-- top 20 steps by wall time --\n"
        "  300.00 s 1200.00 s cpu  rc=0   wt   ./gates/rust_gate.sh\n"
        "  200.00 s 1100.00 s cpu  rc=0   wt   external (plain): GOH_TEST_BIN=x ./tools/pytest.sh\n"
        '   90.00 s  350.00 s cpu  rc=0   wt   "$GOH_PY" ./scripts/release_canary.py\n'
        "    1.50 s  rc=0   tree   gates refuse to pass over an empty tree\n"
        "-- top 20 labels, summed over every run of the label --\n"
        "  999.00 s  rc=0   wt   ./gates/rust_gate.sh\n"
        f"timings: {tmp_path / 'gone'}\n"
    )
    put(q, 40, "GOH.PLAIN", "landed", 45, 55, slot_wait_s=0.0, load_start=30.0, log=plain_log)
    gates = [
        {"kind": "land", "repo": "goh", "epoch": at(29).timestamp(), "wall_s": 780.0,
         "cpu_s": 3500.0},
        {"kind": "land", "repo": "goh", "epoch": at(54).timestamp(), "wall_s": 500.0,
         "cpu_s": 1700.0},
    ]  # fmt: skip
    return q, gates


def test_each_land_gives_back_the_stages_it_was_built_from(queue) -> None:
    q, gates = queue
    inst, plain = land_profile.lands(q, gates)
    assert (inst["item"], inst["mode"], plain["mode"]) == (
        "GOH.INST", "land e2e instrumented", "land e2e plain")  # fmt: skip
    got = {k: v[0] for k, v in inst["stages"].items()}
    assert got == {
        "e2e": 1800.0, "enqueue pre-step": 480.0, "queue wait": 120.0, "retries": 300.0,
        "lander run": 900.0, "  pre-land": 120.0, "  gate": 780.0, "    slot wait": 60.0,
        "    step gates/rust_gate.sh": 600.0, "      > coverage": 550.0,
        "    step scripts/release_canary.py": 100.0,
        "        phase workspace: instrumented build": 40.0,
        "        phase external: GOH_TEST_BIN=x ./tools/pytest.sh": 500.0,
        "  lander other": 0.0,
    }  # fmt: skip
    assert inst["stages"]["      > coverage"][1] == 2900.0
    assert inst["stages"]["  gate"][1] == 3500.0
    got = {k: v[0] for k, v in plain["stages"].items()}
    assert got == {
        "e2e": 900.0, "queue wait": 300.0, "retries": 0.0, "lander run": 600.0,
        "  pre-land": 60.0, "  gate": 500.0, "    slot wait": 0.0,
        "    step gates/rust_gate.sh": 300.0, "      ? external (plain)": 200.0,
        "    step scripts/release_canary.py": 90.0, "  lander other": 40.0,
    }  # fmt: skip
    assert plain["stages"]["      ? external (plain)"][1] == 1100.0


def test_the_table_has_median_max_cpu_share_and_load_per_stage(queue) -> None:
    q, gates = queue
    rows = land_profile.lands(q, gates)
    t = {r["stage"]: r for r in land_profile.table(rows)}
    assert t["e2e"] == {"stage": "e2e", "n": 2, "median_s": 1350.0, "max_s": 1800.0,
                        "cpu_s": None, "share": 1.0, "load": 21.0}  # fmt: skip
    assert t["  gate"]["median_s"] == 640.0 and t["  gate"]["cpu_s"] == 2600.0
    assert t["queue wait"]["share"] == pytest.approx(420 / 2700)
    assert t["enqueue pre-step"]["n"] == 1 and t["enqueue pre-step"]["load"] == 12.0
    order = [r["stage"] for r in land_profile.table(rows, 0.0, land_profile.known_parents(rows))]
    assert order[:8] == [n for n in land_profile.ORDER if n != "earlier attempts"]
    assert order[8:11] == ["    step gates/rust_gate.sh", "      > coverage",
                           "        phase external: GOH_TEST_BIN=x ./tools/pytest.sh"]  # fmt: skip
    assert order[-1] == "  lander other"
    small = [r["stage"] for r in land_profile.table(rows, 150.0)]
    assert "    step scripts/release_canary.py" not in small and "retries" in small


def test_lands_split_by_mode_and_by_the_normal_load_boundary(queue) -> None:
    q, gates = queue
    rows = land_profile.lands(q, gates)
    assert list(land_profile.groups(rows, None)) == ["instrumented", "plain"]
    by = land_profile.groups(rows, at(20))
    assert list(by) == ["instrumented loaded", "plain normal"]
    out = land_profile.render(by)
    assert "── plain normal: 1 lands (GOH.PLAIN)" in out
    assert any(line.startswith("e2e ") and "15.0 min" in line for line in out.splitlines())
    assert land_profile.render({}) == "no landed land in the queue's records"


def test_the_command_reads_the_queue_and_the_ledger(queue, tmp_path, monkeypatch, capsys) -> None:
    q, gates = queue
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "ledger.jsonl").write_text("".join(json.dumps(g) + "\n" for g in gates))
    monkeypatch.setenv("GOH_TIMING_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("GOH_LANDQ_DIR", str(q))
    assert land_profile.main(["--json", "--since", at(30).isoformat()]) == 0
    got = json.loads(capsys.readouterr().out)
    assert list(got) == ["plain"] and got["plain"][0]["median_s"] == 900.0
    assert land_profile.main(["--normal-since", "2026-11-18T15:20Z"]) == 0
    assert "── instrumented loaded: 1 lands" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        land_profile.main(["--since", "yesterday"])
