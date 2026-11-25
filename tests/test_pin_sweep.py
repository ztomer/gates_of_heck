"""goh ship's install only COMMITS the consumers' pins; the pushes run after it (GOH.PINPUSHGATE).

At v0.124.0 the install took 2037 s, 2010 s of it the consumers' full push gates (divoom-control
756, monitor 469, CadGoose 215, token-pool 184), where the commit-only install of v0.121.0 took
86 s; the owner's bound for a whole ship is 5 minutes, 10 at most. Ruling 2026-11-15 (option a):
the install commits each pin and returns; a detached, bounded sweep (lib/pin_sweep.py) pushes each
through its FULL push gate after the ship returns and records each outcome -- pushed, refused with
its log, or not pushed with why -- where `goh ship --status` and the next `goh wf-pin` listing read
it. PINPROPAGATE's guarantee holds: no pin commit is left unpushed in silence.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import _goh_db_fixture as gdb
import pid_owner  # lib/, on the path since conftest imported load_kit
import pytest
import steps_store
from _small_store import estate_state
from conftest import REPO_ROOT, hermetic_env
from test_consumer_pins import SCRIPT, TAG, WF, _ahead, _consumer
from timing_bounds import GiveUp, patience

HOOK = """#!/bin/sh
echo "$(basename "$PWD") ran" >>{log}
{body}
"""


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return hermetic_env(drop_git=True, GOH_ESTATE_STATE=str(tmp_path / "state" / "estate.json"),
                        GOH_LOAD_DIR=str(tmp_path / "load"), GOH_GATE_SLOTS="2",
                        **extra)  # fmt: skip


def _consumers(tmp_path: Path, hooks: dict[str, str]) -> dict[str, Path]:
    out = {}
    for name, body in hooks.items():
        repo = _consumer(
            tmp_path, name, {".github/workflows/ci.yml": WF.format(tag="v0.96.0", ruff="0.0.1")}
        )
        hook = repo / ".git" / "hooks" / "pre-push"
        hook.write_text(HOOK.format(log=tmp_path / "gates.log", body=body))
        hook.chmod(0o755)
        out[name] = repo
    return out


def _wf_pin(tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path / "estate"),
                           "--slug", "owner/gates_of_heck", "--tag", TAG, *args],
                          capture_output=True, text=True, check=False,
                          env=_env(tmp_path, **env), timeout=patience(120),
                          stdin=subprocess.DEVNULL)  # fmt: skip


def _until(what, seconds: float):
    give_up = GiveUp(seconds)
    while not (got := what()):
        assert not give_up.passed(), "never happened"
        time.sleep(0.2)
    return got


def _alive(rec: dict) -> bool:
    """The record's supervisor still runs, by the start time beside its pid: a zombie and a pid
    handed to another process since both read as gone (GOH.STRAYSIGNAL2)."""
    return pid_owner.alive_line(rec["pid_stamp"])


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


@pytest.mark.load_sensitive
def test_the_install_commits_and_returns_before_any_consumer_push_gate_runs(tmp_path):
    repos = _consumers(tmp_path, {"one": "exit 0", "two": "exit 0"})
    ship = tmp_path / "ship.json"  # the ship that runs the install: still running while it does
    gdb.ship(ship, {"status": "running", "pid": os.getpid()})
    r = _wf_pin(tmp_path, "--bump", GOH_SHIP_RESULT=str(ship))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert not (tmp_path / "gates.log").exists(), out  # no push gate ran inside the install
    pins = gdb.pinned(ship) or {}
    assert {k: v["status"] for k, v in pins["repos"].items()} == {
        "one": "committed",
        "two": "committed",
    }, pins
    assert all(_ahead(r) == 1 for r in repos.values()), out
    sweep = Path(pins["sweep"])
    said = sweep.with_suffix(".log")  # the sweep is detached and waiting for the ship to return
    assert _until(lambda: said.exists() and "waiting for the ship" in said.read_text(), 60), out
    assert not (tmp_path / "gates.log").exists() and _read(sweep)["status"] == "pending", out
    gdb.ship(ship, {"status": "ok", "pid": os.getpid()})  # the ship returns
    rec = _until(lambda: (b := _read(sweep)).get("status") == "done" and b, 120)
    assert rec, _read(sweep)
    assert _until(lambda: not _alive(rec), 30), rec  # the supervisor ends with its sweep
    assert {k: v["status"] for k, v in rec["repos"].items()} == {
        "one": "pushed",
        "two": "pushed",
    }, rec
    assert all(_ahead(r) == 0 for r in repos.values()), rec
    assert sorted((tmp_path / "gates.log").read_text().split("\n")[:-1]) == ["one ran", "two ran"]
    rows = steps_store.rows(str(sweep).replace(".json", ".timings.jsonl")) or []
    assert {x["label"] for x in rows} >= {"push one", "push two"}, rows


def test_a_refusing_consumer_is_recorded_with_its_log_and_named_by_the_next_listing(tmp_path):
    repos = _consumers(tmp_path, {"good": "exit 0", "bad": "echo 'gate says no' >&2; exit 1"})
    r = _wf_pin(tmp_path, "--bump", "--sweep", "wait", GOH_SHIP_RESULT=str(tmp_path / "s.json"))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    rec = _read(tmp_path / "s.sweep.json")
    assert rec["status"] == "done" and rec["repos"]["good"]["status"] == "pushed", rec
    bad = rec["repos"]["bad"]
    assert bad["status"] == "refused" and "gate says no" in bad["said"], rec
    assert "gate says no" in Path(bad["log"]).read_text(), bad  # the refusal's whole log is kept
    assert _ahead(repos["bad"]) == 1 and _ahead(repos["good"]) == 0, rec
    listing = _wf_pin(tmp_path)  # the next listing: the workflow is current, the push is not
    said = listing.stdout + listing.stderr
    assert listing.returncode == 1, said
    assert "bad: a pin commit is not pushed" in said and bad["log"] in said, said
    assert "good" not in said, said
    (repos["bad"] / ".git" / "hooks" / "pre-push").write_text("#!/bin/sh\nexit 0\n")
    again = _wf_pin(tmp_path, "--bump", "--sweep", "wait", GOH_SHIP_RESULT=str(tmp_path / "t.json"))
    assert again.returncode == 0, again.stdout + again.stderr
    assert _read(tmp_path / "t.sweep.json")["repos"]["bad"]["status"] == "pushed"
    last = _wf_pin(tmp_path)  # pushed: named no more
    assert last.returncode == 0 and "bad" not in last.stdout + last.stderr, last.stdout


def test_ship_status_shows_the_install_row_and_the_sweeps_rows_apart(tmp_path):
    result = tmp_path / "ship.json"
    sweep = tmp_path / "ship.sweep.json"
    log = tmp_path / "ship.sweep.pushes" / "bad.log"
    install_t = tmp_path / "ship.install.timings.jsonl"
    install_t.write_text(json.dumps({"label": "commit good", "ms": 900.0, "load1": 3.0}) + "\n")
    sweep_t = tmp_path / "ship.sweep.timings.jsonl"
    sweep_t.write_text(json.dumps({"label": "push good", "ms": 400000.0, "load1": 9.5}) + "\n")
    gdb.ship(result, {
        "result": str(result), "status": "ok", "repo": str(tmp_path), "sha": "a" * 40,
        "steps": [{"name": "install", "status": "ok", "seconds": 4.1,
                   "timings": str(install_t)}],
    })  # fmt: skip
    gdb.pins(result, {
        "schema": 2, "tag": TAG, "ruff": "0.0.9", "sweep": str(sweep),
        "repos": {"good": {"status": "committed", "said": "pinned"},
                  "bad": {"status": "committed", "said": "pinned"}},
    })  # fmt: skip
    sweep.write_text(json.dumps({
        "status": "done", "started": "s", "finished": "f", "repos": {
            "good": {"status": "pushed", "said": "pushed 1 pin commit to origin/main"},
            "bad": {"status": "refused", "said": f"NOT pushed: its push gate refused; log {log}",
                    "log": str(log)}},
    }))  # fmt: skip
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    import ship_status

    sys.path.pop(0)
    res = gdb.shipped(result)
    ship_status.carry(res)  # what the ship's install step does once it ends
    ship_status.save(res)
    st = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "ship.py"), "--status",
                         str(result)], capture_output=True, text=True, check=False,
                        env=hermetic_env(drop_git=True), timeout=patience(60),
                        stdin=subprocess.DEVNULL)  # fmt: skip
    out = st.stdout + st.stderr
    assert st.returncode == 0, out
    lines = out.splitlines()
    install = next(i for i, x in enumerate(lines) if "install: ok" in x)
    pins = next(i for i, x in enumerate(lines) if "consumer pins" in x)
    head = next(i for i, x in enumerate(lines) if "push sweep: done" in x)
    assert install < pins < head and "2 committed" in lines[pins], out
    assert "1 of 2 consumers pushed" in lines[head], out
    bad = next(x for x in lines[head:] if "push bad" in x)
    assert "refused" in bad and str(log) in bad, out
    table = next(x for x in lines if "push good" in x and " s " in x)
    assert "sweep" in table and "400.0" in table, out  # the sweep's own row, never the install's
    assert any("commit good" in x and "install" in x for x in lines), out


def test_the_sweep_runs_one_consumer_gate_at_a_time_each_in_a_slot_of_its_own(tmp_path):
    # GOH.LOADSOURCE (2026-11-19, ps sampled each second for 10 min at load 30-70 on 16 cores): a
    # release's sweep is 5000-10300 CPU-s of consumer push gates (54823 s in the day's nine), and
    # it ran as many at once as the machine has gate slots -- two full consumer gates (divoom-
    # control's eight browser workers among them) beside the land and the lanes, background work
    # holding every long slot. The sweep is the release's test against its consumers, never a
    # race: one consumer gate at a time, so it holds at most ONE slot whatever GOH_GATE_SLOTS says.
    # GOH.SWEEPSLOTS: the sweep held ONE slot (its load.py) and ran `width` consumer gates inside
    # it, each nested in that slot -- three heavy gates while admission counted two. Each gate now
    # takes its own slot (its own tools/load.py) and gives it back when it ends, so a land
    # enqueued during a sweep waits at most one consumer gate.
    spans = tmp_path / "spans.log"
    name = '"$(basename "$PWD")"'
    body = (
        f'echo "start {name} $GOH_GATE_SLOT" >>{spans}; sleep 1; '
        f'echo "end {name} $GOH_GATE_SLOT" >>{spans}'
    )
    _consumers(tmp_path, {"one": body, "two": body, "three": body})
    r = _wf_pin(tmp_path, "--bump", "--sweep", "wait", GOH_SHIP_RESULT=str(tmp_path / "s.json"))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    rec = _read(tmp_path / "s.sweep.json")
    assert {v["status"] for v in rec["repos"].values()} == {"pushed"}, rec
    said = [line.split(maxsplit=1) for line in spans.read_text().splitlines()]
    assert len(said) == 6, said
    for start, end in zip(said[::2], said[1::2], strict=True):  # each gate ends before the next
        assert (start[0], end[0]) == ("start", "end") and start[1] == end[1], said
    holders = {of.split()[-1] for _, of in said}
    assert len(holders) == 3 and all(h.isdigit() for h in holders), said  # one per consumer gate


@pytest.mark.load_sensitive
def test_a_newer_sweep_takes_over_an_older_ones_unstarted_pushes_and_waits_for_it(tmp_path):
    # GOH.LOADSOURCE: releases come every five lands now (rules.ship_every_lands), and a sweep
    # spans 20-58 min; two sweeps ran at once at 19:56 today and four consumer gates ran twice for
    # one pin. A newer sweep's push carries every older pin commit of a repo it lists, so the older
    # sweep leaves those repos to it (`superseded`, naming it) and the newer one starts its gates
    # only once the older has ended: one sweep at a time, whatever the release rate.
    repos = _consumers(tmp_path, {"one": "exit 0", "two": "exit 0"})
    ship = tmp_path / "old.json"  # the older release's ship, still running: its sweep waits
    gdb.ship(ship, {"status": "running", "pid": os.getpid()})
    try:
        _two_sweeps(tmp_path, repos, ship)
    finally:  # a red run never leaves the older supervisor waiting an hour on this test's pid
        gdb.ship(ship, {"status": "ok", "pid": os.getpid()})


def _two_sweeps(tmp_path: Path, repos: dict[str, Path], ship: Path) -> None:
    r = _wf_pin(tmp_path, "--bump", GOH_SHIP_RESULT=str(ship))
    assert r.returncode == 0, r.stdout + r.stderr
    older = tmp_path / "old.sweep.json"
    assert _until(lambda: _read(older).get("pid"), 30), _read(older)
    again = _wf_pin(tmp_path, "--bump", GOH_SHIP_RESULT=str(tmp_path / "new.json"))
    assert again.returncode == 0, again.stdout + again.stderr
    newer = tmp_path / "new.sweep.json"
    said = newer.with_suffix(".log")
    assert _until(lambda: said.exists() and "waiting for the older sweep" in said.read_text(), 60)
    assert not (tmp_path / "gates.log").exists(), said.read_text()  # no gate while the older lives
    gdb.ship(ship, {"status": "ok", "pid": os.getpid()})  # the older ship returns
    old = _until(lambda: (b := _read(older)).get("status") == "done" and b, 120)
    assert {v["status"] for v in old["repos"].values()} == {"superseded"}, old
    assert all(str(newer) in v["said"] for v in old["repos"].values()), old
    new = _until(lambda: (b := _read(newer)).get("status") == "done" and b, 120)
    assert {v["status"] for v in new["repos"].values()} == {"pushed"}, new
    assert all(_ahead(x) == 0 for x in repos.values()), new
    assert sorted((tmp_path / "gates.log").read_text().splitlines()) == ["one ran", "two ran"]
    assert pin_sweep_unpushed(tmp_path) == {}, "a superseded repo is no unpushed pin"
    assert _until(lambda: not _alive(old) and not _alive(new), 30), (old, new)


def pin_sweep_unpushed(tmp_path: Path) -> dict:
    return estate_state(tmp_path / "state" / "pin_push.json").get("repos") or {}


def test_a_sweep_that_never_got_its_supervisor_holds_no_newer_sweep_back(tmp_path, monkeypatch):
    # GOH.LOADSOURCE: start() registers a sweep before it starts the supervisor, so a record with
    # no pid yet counts as running -- but only while it is young. A start whose supervisor never
    # started (its fork refused) leaves a pending record with no pid for good, and every newer
    # sweep would wait out its whole bound behind it.
    monkeypatch.setenv("GOH_ESTATE_STATE", str(tmp_path / "state" / "estate.json"))
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    import pin_sweep

    old, new = tmp_path / "a.sweep.json", tmp_path / "b.sweep.json"
    for rec in (old, new):
        rec.write_text(json.dumps({"status": "pending", "created": pin_sweep.now(), "repos": {}}))
        pin_sweep.register(rec, ["/r"])
    assert pin_sweep.older(new) == str(old)  # still starting: the newer one waits for it
    stale = pin_sweep.dt.datetime.now(pin_sweep.dt.UTC) - pin_sweep.dt.timedelta(minutes=5)
    old.write_text(json.dumps({"status": "pending", "created": stale.isoformat(), "repos": {}}))
    assert pin_sweep.older(new) is None
    assert pin_sweep.newer(old, "/r") == str(new)  # the newer one is young and pid-less: live
