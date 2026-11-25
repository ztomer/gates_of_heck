"""`goh budget gates`: every estate repo's last measured push gate, per check (CLS.CIPROFILE).

The CIP method (docs/map.md) starts from "which repo's push gate is slow, and on what check".
The answer is already on the machine: the timing ledger's `push` rows (lib/timing_ledger.py), each
repo's newest one, and the costliest steps a row carries when the gate ran with GOH_TIMINGS. The
estate is the registry (lib/estate_registry.py), so a repo with NO recorded run is named, never
silently absent. Read only: nothing is gated, nothing is backfilled.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import budget
import gate_times
import timing_ledger


def row(repo: str, wall: float, at: str, *, ok: bool = True, steps=None, kind: str = "push"):
    r = timing_ledger.row_for(
        kind, timing_ledger.repo_name(repo), wall, load1=7.5, ok=ok, top_steps=steps,
        read_load=False, at=gate_times.parse_at(at), sha="abc123",
    )  # fmt: skip
    return json.dumps(r)


def estate(tmp_path: Path, monkeypatch, repos: list[str], ledger: list[str]) -> None:
    state = tmp_path / "estate" / "estate_audit.json"
    state.parent.mkdir()
    (state.parent / "installed_repos").write_text(
        "".join(f"/x/{name}\n" for name in repos) + "not-absolute\n"
    )
    monkeypatch.setenv("GOH_ESTATE_STATE", str(state))
    tdir = tmp_path / "t"
    tdir.mkdir()
    (tdir / "ledger.jsonl").write_text("\n".join(ledger) + "\n")
    monkeypatch.setenv("GOH_TIMING_DIR", str(tdir))


LEDGER = [
    row("alpha", 900.0, "2026-11-18T10:00:00", steps=[["rust gate", 700.0], ["pytest", 150.0]]),
    row("alpha", 640.0, "2026-11-19T10:00:00"),  # newest, but no per-check record
    row("beta", 120.0, "2026-11-19T09:00:00", ok=False, steps=[["lint", 90.0]]),
    row("gates_of_heck", 300.0, "2026-11-19T08:00:00", steps=[["coverage", 250.0]]),
    row("alpha", 30.0, "2026-11-19T11:00:00", kind="land"),  # a land is not a push gate
    row("stranger", 50.0, "2026-11-19T11:00:00"),  # not in the estate: not shown
]


def test_each_estate_repo_shows_its_newest_push_gate_and_its_last_per_check_record(
    tmp_path, monkeypatch
) -> None:
    estate(tmp_path, monkeypatch, ["alpha", "beta", "gates_of_heck", "gamma"], LEDGER)
    snap = gate_times.snapshot()
    got = {r["repo"]: r for r in snap["repos"]}
    assert [r["repo"] for r in snap["repos"]] == ["alpha", "goh", "beta"]  # slowest first
    assert got["alpha"]["wall_s"] == 640.0
    assert got["alpha"]["target"] == "over"  # over 10 min
    assert got["alpha"]["steps"] == [["rust gate", 700.0], ["pytest", 150.0]]
    assert got["alpha"]["steps_at"] == "2026-11-18T10:00:00+00:00"  # an older run's checks, dated
    assert got["beta"]["ok"] is False and got["beta"]["target"] == "aim"  # within 5 min
    assert got["goh"]["target"] == "aim" and got["goh"]["steps"] == [["coverage", 250.0]]
    assert snap["no_run"] == ["gamma"]
    assert snap["unreadable"] == ["not-absolute"]


def test_the_table_names_the_target_the_checks_and_the_repos_with_no_run(
    tmp_path, monkeypatch, capsys
) -> None:
    estate(tmp_path, monkeypatch, ["alpha", "beta", "gamma", "delta"], LEDGER)
    assert budget.main(["gates"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    alpha = next(i for i, line in enumerate(lines) if " alpha " in line)
    assert lines[alpha].startswith("✗") and "640" in lines[alpha] and "over 10 min" in lines[alpha]
    assert "rust gate 700 s" in lines[alpha + 1] and "2026-11-18" in lines[alpha + 1]
    assert "(failed)" in next(line for line in lines if " beta " in line)
    assert "no recorded push gate: delta, gamma" in out
    assert "tools/gate_profile.sh" in out  # the way to get a per-check record


def test_a_repo_whose_runs_never_recorded_checks_says_so(tmp_path, monkeypatch, capsys) -> None:
    estate(tmp_path, monkeypatch, ["alpha"], [row("alpha", 200.0, "2026-11-19T10:00:00")])
    assert budget.main(["gates", "--json"]) == 0
    snap = json.loads(capsys.readouterr().out)
    assert snap["repos"][0]["steps"] == [] and snap["repos"][0]["steps_at"] is None
    assert budget.main(["gates"]) == 0
    assert "per check: not recorded" in capsys.readouterr().out


def test_every_full_push_gate_records_its_checks_and_leaves_no_timings_file(tmp_path) -> None:
    """The per-check column is only as good as its writer: a push gate that ran without
    GOH_TIMINGS wrote a wall and no checks, so 26 of 28 estate repos had no per-check record. The
    push gate now times its own steps into a file of its own (removed after the row) when nobody
    handed it one."""
    from test_push_gate import PUSH_GATE, _repo

    repo = _repo(tmp_path)
    # A gate that leaves structural to the push: its steps are the checks the row must name.
    (repo / "tools" / "gate.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    git(repo, "commit", "-qam", "a gate with no structural layer")
    sha = git(repo, "rev-parse", "HEAD").strip()
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    env = hermetic_env(
        GATE_REPORT=str(tmp_path / "report.txt"),
        GOH_PUSH_LOGS=str(tmp_path / "logs"),
        GOH_PUSH_WORKTREES=str(tmp_path / "exports"),
        GOH_TIMING_DIR=str(tmp_path / "ledger"),
        TMPDIR=str(tmp),
    )
    r = subprocess.run(
        ["bash", str(PUSH_GATE), "origin"], cwd=repo, env=env, text=True,
        input=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n",
        capture_output=True, timeout=patience(120), check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    (row,) = timing_ledger.read(tmp_path / "ledger" / "ledger.jsonl")
    assert row["kind"] == "push" and row.get("top_steps"), row
    assert not list(tmp.rglob("goh-push-timings.*")), "the push gate left its timings file"
