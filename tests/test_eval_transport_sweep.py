"""eval_transport — the resumable sweep: state on disk, and the loop that drives it.

Split from `test_eval_transport.py` by concern rather than for length alone: that file is the
TRANSPORT (what goes out, what comes back, and the two guards on a reply — the parse-rate floor and
the whole-call deadline); this one is everything that has to survive the process ending, which is a
different failure mode with a different evidence: the state file is read back by a NEW process
after a simulated crash, not answered by a server.

The unit under test here is `SweepState` plus `run_loop`, so `fake_api` arrives only where a loop
needs an endpoint to exist; it is imported (not re-declared) so this file cannot grow a second,
different fake endpoint.
"""

import json
import os
import sys

import pytest

from _eval_transport_kit import (
    REPO_ROOT,
    _mk_state,
    _rewire,  # noqa: F401  (autouse: rewinds the fake endpoint between tests)
    et,
    fake_api,  # noqa: F401
)

# ---- resumable sweep state ---------------------------------------------------


def test_start_records_planned_units(tmp_path):
    st = _mk_state(tmp_path)
    st.start(["m1", "m2"], ["t1", "t2"])
    summary = st.summary()
    assert summary["planned"] == 4
    assert summary["done"] == 0 and not summary["complete"]


def test_mark_done_then_resume_skips_done_units(tmp_path):
    st = _mk_state(tmp_path)
    units = [("m1", "t1"), ("m1", "t2"), ("m2", "t1")]
    st.start(sorted({u[0] for u in units}), sorted({u[1] for u in units}))
    st.mark_done("m1", "t1")
    st.mark_done("m2", "t1")
    pending = st.resume(sorted({m for m, _ in units}), sorted({t for _, t in units}))
    # planned grid is the full cross product: m1t2 and m2t2 still open
    assert pending == [("m1", "t2"), ("m2", "t2")]
    assert st.summary()["complete"] is False
    st.mark_done("m1", "t2")
    st.mark_done("m2", "t2")
    assert st.summary()["complete"] is True


def test_done_marker_survives_simulated_crash_mid_write(tmp_path, monkeypatch):
    """Crash between tmp-write and atomic replace: readers must still see the
    last complete state, never a half-written file."""
    path = tmp_path / "sweep_state.json"
    st = et.SweepState(path)
    st.start(["m1"], ["t1", "t2"])
    st.mark_done("m1", "t1")

    real_replace = os.replace

    def crashy_replace(src, dst):
        raise RuntimeError("simulated crash (power loss)")

    monkeypatch.setattr(et.os, "replace", crashy_replace)
    with pytest.raises(RuntimeError, match="simulated crash"):
        st.mark_done("m1", "t2")

    monkeypatch.setattr(et.os, "replace", real_replace)
    fresh = et.SweepState(path)  # a NEW process reads the file off disk
    assert fresh.resume(["m1"], ["t1", "t2"]) == [("m1", "t2")]
    assert json.loads(path.read_text())["done"]["m1|t1"]["status"] == "done"


def test_truncated_run_looks_truncated(tmp_path):
    """The ztools lesson: a sweep killed mid-run must be VISIBLY truncated,
    not silently look complete."""
    st = _mk_state(tmp_path)
    st.start(["m1", "m2"], ["t1"])
    st.mark_done("m1", "t1")
    # ... process dies here; nothing marked m2/t1 ...
    reopened = et.SweepState(tmp_path / "sweep_state.json")
    s = reopened.summary()
    assert (s["done"], s["planned"]) == (1, 2)
    assert s["complete"] is False
    assert reopened.resume(["m1", "m2"], ["t1"]) == [("m2", "t1")]


# ---- run_loop -----------------------------------------------------------------


def test_run_loop_runs_pending_units_and_marks_done(tmp_path, fake_api):
    t = et.EvalTransport(base_url=fake_api)
    st = _mk_state(tmp_path)
    st.start(["m-alpha"], ["t1", "t2"])
    ran = []
    note = et.run_loop(
        "m-alpha",
        ["t1", "t2"],
        lambda model, task: ran.append((model, task)) or "ok",
        state=st,
    )
    assert ran == [("m-alpha", "t1"), ("m-alpha", "t2")]
    assert note["done"] == 2 and note["errors"] == 0


def test_run_loop_skips_already_done_units(tmp_path, fake_api):
    st = _mk_state(tmp_path)
    st.start(["m-alpha"], ["t1"])
    st.mark_done("m-alpha", "t1")
    ran = []
    note = et.run_loop(
        "m-alpha",
        ["t1"],
        lambda model, task: ran.append(task),
        state=st,
    )
    assert ran == []
    assert note["done"] == 1  # carried over from prior run


def test_run_loop_records_unit_failure_without_aborting(tmp_path, fake_api):
    st = _mk_state(tmp_path)
    st.start(["m-alpha"], ["t1", "t2"])

    def unit(model, task):
        if task == "t1":
            raise ValueError("task exploded")
        return "fine"

    note = et.run_loop("m-alpha", ["t1", "t2"], unit, state=st)
    assert note["errors"] == 1
    assert note["done"] == 1  # only t2
    # the failed unit stays pending so a resumed run retries it
    assert st.resume(["m-alpha"], ["t1", "t2"]) == [("m-alpha", "t1")]


def test_run_loop_resumes_after_crash(tmp_path):
    st = et.SweepState(tmp_path / "sweep_state.json")
    st.start(["m1"], ["t1", "t2"])

    class Crash(BaseException):
        pass  # BaseException: models process death (SIGINT/kill), not a
        # per-unit failure — run_loop records Exception misses but must let
        # a dead process die with its DONE markers intact

    def dying_unit(model, task):
        st.mark_done(model, "t1")  # first unit completes...
        raise Crash("process dies mid-sweep")  # ...then everything stops

    with pytest.raises(Crash):
        et.run_loop("m1", ["t1", "t2"], dying_unit, state=st)

    reopened = et.SweepState(tmp_path / "sweep_state.json")
    ran = []
    et.run_loop(
        "m1",
        ["t1", "t2"],
        lambda model, task: ran.append(task),
        state=reopened,
    )
    assert ran == ["t2"]


# ── round-3 hardening: concurrent writers, corrupt files, key collisions ─────


def test_two_process_writers_lose_no_markers(tmp_path):
    """Regression (2026-08-26): concurrent processes doing read-modify-write
    lost 385 of 400 markers (each writer's stale read clobbered the other's).
    With the flock-serialized update cycle every marker must survive."""
    import subprocess as sp

    path = tmp_path / "sweep_state.json"
    workers, per_worker = 4, 40
    st = et.SweepState(path)
    st.start([f"w{i}" for i in range(workers)], [f"t{j:03d}" for j in range(per_worker)])

    script = "\n".join(
        [
            "import sys",
            f"sys.path.insert(0, {str(REPO_ROOT)!r})",
            "from lib.eval_transport import SweepState",
            f"st = SweepState({str(path)!r})",
            "wid = sys.argv[1]",
            f"for j in range({per_worker}):",
            "    st.mark_done(f'w{wid}', f't{j:03d}')",
        ]
    )
    procs = [
        sp.Popen(
            [sys.executable, "-c", script, str(i)],
            cwd=str(tmp_path),
            stdout=sp.PIPE,
            stderr=sp.PIPE,
            text=True,
        )
        for i in range(workers)
    ]
    for p in procs:
        out, errout = p.communicate(timeout=120)
        assert p.returncode == 0, f"worker died: {errout}"

    fresh = et.SweepState(path)  # a NEW process reads what landed on disk
    s = fresh.summary()
    assert s["done"] == workers * per_worker, (
        f"lost updates: {s['done']}/{workers * per_worker} markers survived"
    )
    assert (
        fresh.resume([f"w{i}" for i in range(workers)], [f"t{j:03d}" for j in range(per_worker)])
        == []
    )


def test_corrupt_state_file_raises_named_error_not_raw_exception(tmp_path):
    path = tmp_path / "sweep_state.json"
    path.write_text('{"version": 1, "planned": ["m1|t1"), ')  # truncated JSON
    st = et.SweepState(path)
    with pytest.raises(et.SweepStateCorrupt, match="corrupt.*sweep_state"):
        st.resume(["m1"], ["t1"])
    with pytest.raises(et.SweepStateCorrupt):
        st.mark_done("m1", "t1")

    # Non-JSON garbage (e.g. an HTML error page written over it) too:
    path.write_text("<html>gateway timeout</html>")
    with pytest.raises(et.SweepStateCorrupt):
        st.summary()


def test_corrupt_state_discard_is_explicit_and_starts_fresh(tmp_path):
    path = tmp_path / "sweep_state.json"
    path.write_text("{not json at all")
    st = et.SweepState(path, on_corrupt="discard")
    assert st.resume(["m1"], ["t1"]) == [("m1", "t1")]  # nothing carried over
    st.mark_done("m1", "t1")  # ...and the file is writable again
    assert st.summary()["done"] == 1
    assert json.loads(path.read_text())["done"]["m1|t1"]["status"] == "done"


def test_on_corrupt_rejects_unknown_policy(tmp_path):
    with pytest.raises(ValueError, match="on_corrupt"):
        et.SweepState(tmp_path / "x.json", on_corrupt="yolo")


def test_separator_collision_pairs_stay_distinct(tmp_path):
    # Regression (2026-08-26): naive model|task join made ("a|b", "c") and
    # ("a", "b|c") the same marker key.
    k1 = et.SweepState._key("a|b", "c")
    k2 = et.SweepState._key("a", "b|c")
    assert k1 != k2
    # End-to-end: marking one pair must not complete its collision twin.
    st = _mk_state(tmp_path)
    st.start(["a|b", "a"], ["c", "b|c"])
    st.mark_done("a|b", "c")
    pending = st.resume(["a|b", "a"], ["c", "b|c"])
    assert ("a", "b|c") in pending
    assert ("a|b", "c") not in pending


def test_plain_keys_are_byte_identical_to_v1_format():
    # Old state files must stay resumable: no % or | in names → same key.
    assert et.SweepState._key("m-alpha", "task1") == "m-alpha|task1"
