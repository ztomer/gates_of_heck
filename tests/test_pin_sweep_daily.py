"""The daily push sweep (GOH.PINSWEEP), in memory: goh.db is two connections, each consumer's git
and push gate a fake, so nothing is pushed, spawned or waited for."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import consumer_pins
import pid_owner
import pin_push
import pin_sweep
import push_hold
import push_sweep_rows as rows
import pytest
from _kv_fixture import Held
from _small_store import estate_state
from bounded_run import Outcome
from test_push_hold import ANDROID, CI, MOVED

NAMES = ("alpha", "beta", "gamma")
DEAD = "999999 Thu Jan  1 00:00:00 1970"


class Estate:
    """The consumers' git and push gates: what each is ahead by, its tip, when its oldest pin was
    made, what its gate prints, and the pushes that reached a gate, in order."""

    def __init__(self) -> None:
        self.made = dict.fromkeys(NAMES, 1.0)
        self.tip = dict.fromkeys(NAMES, "t1")
        self.says = dict.fromkeys(NAMES, "")
        self.closure = "c1"
        self.owner: set[str] = set()
        self.gated: list[str] = []

    def lay(self, monkeypatch) -> Estate:
        owner = self.owner
        monkeypatch.setattr(
            consumer_pins,
            "ahead",
            lambda r, fetch=False: (
                "origin/main",
                [("o", "feat: theirs")] * (r.name in owner) + [("p", "ci: pin")],
            ),
        )
        monkeypatch.setattr(consumer_pins, "is_pin_commit", lambda _r, sha, _s: sha == "p")
        monkeypatch.setattr(consumer_pins, "oldest", lambda r: self.made[r.name])
        monkeypatch.setattr(consumer_pins.estate_commit, "git",
                            lambda *a: subprocess.CompletedProcess(a, 0, "0", ""))  # fmt: skip
        monkeypatch.setattr(consumer_pins.bounded_run, "run_step", self.gate)
        monkeypatch.setattr(push_hold, "tip", lambda r: self.tip[r.name])
        monkeypatch.setattr(push_hold, "closure", lambda *_: self.closure)
        monkeypatch.setattr(pin_push.steps_store, "ingest_all", lambda _p: None)
        return self

    def gate(self, argv, _timeout, _grace, label: str, log: str) -> Outcome:
        name = label.split()[-1]
        self.gated.append(name)
        mark = Path(argv[argv.index("sh") + 3])  # what load.py's admission stamps
        mark.write_text(str(int(time.time()) + 7))
        Path(log).write_text(self.says[name])
        return Outcome(int(bool(self.says[name])), [], False)


@pytest.fixture
def estate(tmp_path, monkeypatch) -> Estate:
    Held().lay(monkeypatch)
    monkeypatch.setenv("GOH_ESTATE_STATE", str(tmp_path / "estate.json"))
    monkeypatch.setattr(consumer_pins, "_deadline", [0.0])
    return Estate().lay(monkeypatch)


def sweep(tmp_path: Path, n: int, names=NAMES, **more) -> Path:
    path = tmp_path / f"s{n}.sweep.json"
    rows.put(path, {
        "run": "r", "status": "pending", "created": float(n), "quiet": 60.0, "every": 600.0,
        "budget": 900.0, "log": str(tmp_path / f"s{n}.log"), **more,
        "repos": {w: {"repo": f"/p/{w}", "status": "pending"} for w in names}})  # fmt: skip
    return path


def swept(tmp_path: Path, n: int, names=NAMES) -> dict:
    """Sweep N run to its end: its repos."""
    path = sweep(tmp_path, n, names)
    assert rows.begin(path, "r", float(n)) == ("go", "")
    pin_push.run(path, "r")
    rows.fail(path, "r", "", n + 0.5, "done", 0)
    assert not list(tmp_path.rglob("*.sweep.json")), "a run wrote a record file"
    return rows.read(path)["repos"]


def ledger(tmp_path: Path) -> dict:
    return estate_state(tmp_path / "pin_push.json").get("repos") or {}


def test_the_one_waiting_longest_is_pushed_first_and_its_slot_wait_is_kept(
    estate, tmp_path
) -> None:
    estate.made |= {"gamma": 1.0, "alpha": 3.0, "beta": 2.0}
    got = swept(tmp_path, 1)
    assert estate.gated == ["gamma", "beta", "alpha"]
    assert {r["status"] for r in got.values()} == {"pushed"} and ledger(tmp_path) == {}
    assert all(6 <= r["slot_wait_s"] <= 8 and "seconds" in r and r["at"] >= r["started"]
               for r in got.values()), got  # fmt: skip


def test_a_same_tip_refusal_is_named_not_retried_and_a_moved_tip_is(estate, tmp_path) -> None:
    estate.says["beta"] = CI
    first = swept(tmp_path, 1)["beta"]
    assert first["status"] == "refused" and first["tip"] == "t1"
    assert list(first["steps"].values()) == ["c1", "c1"] and ANDROID in first["steps"]
    said = ledger(tmp_path)["/p/beta"]
    assert ANDROID in said["said"] and "the Android build" in said["said"], said
    assert "load: spinners" not in said["said"] and said["log"].endswith("beta.log")
    for n in (2, 3):  # and again: a held turn is no verdict
        got = swept(tmp_path, n, ("beta",))["beta"]
        assert got["status"] == "held" and ANDROID in got["said"] and "steps" not in got
    assert estate.gated.count("beta") == 1
    named = ledger(tmp_path)["/p/beta"]
    assert named["status"] == "held" and named["log"] == first["log"], named
    estate.tip["beta"], estate.says["beta"] = "t2", ""
    assert swept(tmp_path, 4, ("beta",))["beta"]["status"] == "pushed"
    assert estate.gated.count("beta") == 2 and ledger(tmp_path) == {}


def test_goh_moving_what_the_step_reads_is_asked_again(estate, tmp_path) -> None:
    estate.says["beta"] = CI
    swept(tmp_path, 1, ("beta",))
    estate.closure = "c2"
    assert swept(tmp_path, 2, ("beta",))["beta"]["status"] == "refused"
    assert estate.gated == ["beta", "beta"]


def test_a_push_no_gate_judged_is_asked_again(estate, tmp_path) -> None:
    estate.says["beta"] = MOVED
    got = swept(tmp_path, 1, ("beta",))["beta"]
    assert got["status"] == "refused" and "judged by no gate" in got["said"] and "tip" not in got
    assert swept(tmp_path, 2, ("beta",))["beta"]["status"] == "refused"
    assert len(estate.gated) == 2


def test_an_owners_commit_is_never_pushed_and_named(estate, tmp_path) -> None:
    estate.owner.add("alpha")
    got = swept(tmp_path, 1)["alpha"]
    assert got["status"] == "committed" and "feat: theirs" in got["said"]
    assert "alpha" not in estate.gated and "/p/alpha" in ledger(tmp_path)


class Clock:
    def __init__(self, at: float) -> None:
        self.at, self.slept = at, []

    def __call__(self) -> float:
        return self.at

    def sleep(self, s: float) -> None:
        assert len(self.slept) < 500, "never due"
        self.slept.append(s)
        self.at += s


def test_a_sweep_waits_until_it_is_due(estate, tmp_path) -> None:
    path, clock = sweep(tmp_path, 100), Clock(100.0)
    assert pin_sweep.wait_due(path, "r", clock, clock.sleep) is True
    assert clock.at == pytest.approx(160.0) and max(clock.slept) <= pin_sweep.DUE_POLL
    assert rows.read(path)["due"] == 160.0 and estate.gated == []


def test_a_release_meanwhile_takes_the_wait_over_and_rides_one_push(estate, tmp_path) -> None:
    old, clock = sweep(tmp_path, 100), Clock(100.0)
    waited = []

    def sleep(s: float) -> None:
        waited.append(s)
        clock.sleep(s)
        sweep(tmp_path, 130)  # a newer release, before the old one was due

    assert pin_sweep.wait_due(old, "r", clock, sleep) is True and len(waited) == 1
    assert pin_sweep._begin(old, "r") is False
    got = rows.read(old)
    assert got["status"] == "done" and {r["status"] for r in got["repos"].values()} == {
        "superseded"}  # fmt: skip
    assert estate.gated == [] and ledger(tmp_path) == {}
    sweep(tmp_path, 130, run="another start")
    assert pin_sweep.wait_due(tmp_path / "s130.sweep.json", "r", clock, clock.sleep) is False


def test_a_dead_supervisors_record_settles(estate, tmp_path) -> None:
    gone = sweep(tmp_path, 100, supervisor=DEAD)
    young = sweep(tmp_path, 200, ("delta",))
    alive = sweep(tmp_path, 300, ("eps",), supervisor=pid_owner.stamp_line(os.getpid()))
    orphan = sweep(tmp_path, 400, ("zeta",), supervisor=DEAD,
                   runner=pid_owner.stamp_line(os.getpid()))  # fmt: skip
    rows.outcome(gone, "alpha", status="pushed", at=101.0)
    rows.outcome(gone, "beta", status="pushing", at=102.0)
    at = 200.0 + pin_sweep.START_GRACE / 2
    assert pin_sweep.settle(at) == [str(gone)] and pin_sweep.settle(at) == []
    rec = rows.read(gone)
    assert rec["status"] == "failed" and "supervisor ended" in rec["why"]
    assert {w: r["status"] for w, r in rec["repos"].items()} == {
        "alpha": "pushed", "beta": "committed", "gamma": "committed"}  # fmt: skip
    assert set(ledger(tmp_path)) == {"/p/beta", "/p/gamma"}
    for path in (young, alive, orphan):  # its start, its supervisor or its pushes still there
        assert rows.read(path)["status"] == "pending", path
    assert pin_sweep.settle(at + pin_sweep.START_GRACE) == [
        str(young)
    ]  # its supervisor never started


def test_a_dead_running_sweep_holds_no_newer_one_back(estate, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pin_sweep, "BOUND", 2)
    monkeypatch.setattr(pin_sweep, "POLL", 0.05)
    old = sweep(tmp_path, 1, ("alpha",), supervisor=DEAD)
    assert rows.begin(old, "r", 2.0) == ("go", "")
    new = sweep(tmp_path, 3, ("beta",), supervisor=pid_owner.stamp_line(os.getpid()))
    assert pin_sweep._begin(new, "r") is True and rows.read(old)["status"] == "failed"


def test_a_live_running_sweep_is_waited_for_and_no_longer_than_its_bound(
    estate, tmp_path, monkeypatch
) -> None:
    mine = pid_owner.stamp_line(os.getpid())
    assert rows.begin(sweep(tmp_path, 1, ("alpha",), supervisor=mine), "r", 2.0) == ("go", "")
    new = sweep(tmp_path, 3, ("beta",), supervisor=mine)
    monkeypatch.setattr(pin_sweep, "BOUND", 0.0)
    assert pin_sweep._begin(new, "r") is False
    assert rows.read(new)["status"] == "failed" and "/p/beta" in ledger(tmp_path)


def test_a_waiting_supervisor_keeps_the_copy_of_goh_it_runs(estate, tmp_path, monkeypatch) -> None:
    """Its code is an export that is pruned an hour after its last use, and the wait is hours; the
    pushes it starts run the checkout's HEAD."""
    used = tmp_path / ".goh-used"
    used.write_text("")
    os.utime(used, (1, 1))
    monkeypatch.setattr(pin_sweep, "GOH", tmp_path)
    pin_sweep._nap(0)
    assert used.stat().st_mtime > 1  # this use's, not the old one's
    used.unlink()
    pin_sweep._nap(0)  # a checkout has none
    assert not used.exists()
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "pin_sweep.py").write_text("")
    monkeypatch.setenv("GOH_LIVE_ROOT", str(tmp_path))
    assert pin_sweep._me() == str(tmp_path / "lib" / "pin_sweep.py")
    monkeypatch.setenv("GOH_LIVE_ROOT", str(tmp_path / "none"))
    assert pin_sweep._me() == str(Path(pin_sweep.__file__).resolve())
    assert pin_sweep._me("pin_push.py") == str(Path(pin_push.__file__).resolve())


def test_a_live_sweeps_name_is_never_taken_from_it(estate, tmp_path) -> None:
    result = tmp_path / "wf_pin.json"
    assert pin_sweep.place(result) == tmp_path / "wf_pin.sweep.json"
    rows.put(pin_sweep.place(result), {
        "run": "r", "status": "running", "created": 1.0, "quiet": 0.0, "every": 0.0, "budget": 1.0,
        "supervisor": pid_owner.stamp_line(os.getpid()), "repos": {}})  # fmt: skip
    other = pin_sweep.place(result)
    assert other != tmp_path / "wf_pin.sweep.json" and other.name.endswith(".sweep.json")


def test_the_status_says_when_a_waiting_sweep_is_due(estate, tmp_path) -> None:
    path = sweep(tmp_path, 100, due=160.0, supervisor="77 t")
    head = pin_sweep.lines({"consumer_pins": {"sweep": str(path)}})[0][1]
    assert "pending (due 1970-01-01T00:02:40+00:00; pid 77;" in head, head
    assert "0 of 3 consumers pushed" in head
    (mark, said), *more = pin_sweep.lines({"consumer_pins": {"sweep": str(tmp_path / "none")}})
    assert mark == "err" and "no record" in said and not more
