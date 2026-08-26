"""eval_transport — chat-completion round-trip, parse-rate guardrail,
resumable sweep state. HTTP is exercised against a REAL local server
(http.server thread), not a mock of the transport itself.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib import eval_transport as et  # noqa: E402


# ---- fake OpenAI-compatible endpoint ----------------------------------------


class _FakeAPI(BaseHTTPRequestHandler):
    """Canned /v1/models + /v1/chat/completions. Behaviour knobs are class
    attrs so each test can rewire the endpoint without a new server."""

    models = ["m-alpha", "m-beta"]
    # content returned for chat completions; may be a str or a callable
    # (request_body_dict) -> str
    content_factory = lambda body: json.dumps({"echo": body["model"]})  # noqa: E731
    http_status = 200
    requests: list = []  # every parsed POST body lands here
    last_headers: dict = {}  # request headers of the last POST

    def log_message(self, *a):  # silence the test runner
        pass

    def _send_json(self, obj, status=200):
        payload = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send_json({"data": [{"id": m} for m in _FakeAPI.models]})
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send_json({"error": "not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        _FakeAPI.requests.append(body)  # read exactly once — never re-consumed
        _FakeAPI.last_headers = {k.lower(): v for k, v in self.headers.items()}
        if _FakeAPI.http_status != 200:
            self._send_json({"error": "boom"}, status=_FakeAPI.http_status)
            return
        content = _FakeAPI.content_factory(body)
        self._send_json({
            "id": "chatcmpl-x",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content}}
            ],
            "usage": {"completion_tokens": len(content)},
        })


@pytest.fixture
def fake_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAPI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/v1"
    yield base
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def _rewire(fake_api):
    _FakeAPI.models = ["m-alpha", "m-beta"]
    _FakeAPI.http_status = 200
    _FakeAPI.content_factory = lambda body: json.dumps({"echo": body["model"]})
    _FakeAPI.requests = []
    yield


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("EVAL_BASE_URL", "OPENAI_BASE_URL", "EVAL_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


# ---- round-trip --------------------------------------------------------------


def test_chat_round_trip_extracts_text_and_usage(fake_api):
    t = et.EvalTransport(base_url=fake_api, api_key="k-test")
    out = t.chat("m-alpha", [{"role": "user", "content": "hello"}])
    assert out["text"] == json.dumps({"echo": "m-alpha"})
    assert out["tokens"] == len(out["text"])
    assert out["elapsed"] >= 0.0


def test_chat_sends_bearer_key_and_model(fake_api):
    t = et.EvalTransport(base_url=fake_api, api_key="sekrit")
    out = t.chat("m-beta", [{"role": "user", "content": "hi"}], temperature=0.2)
    assert out["text"]
    assert len(_FakeAPI.requests) == 1
    sent = _FakeAPI.requests[0]
    assert sent["model"] == "m-beta"
    assert sent["temperature"] == 0.2
    assert _FakeAPI.last_headers.get("authorization") == "Bearer sekrit"


def test_discover_models_lists_endpoint_ids(fake_api):
    t = et.EvalTransport(base_url=fake_api)
    assert t.discover_models() == ["m-alpha", "m-beta"]


def test_http_error_raises_named_transport_error(fake_api):
    _FakeAPI.http_status = 500
    t = et.EvalTransport(base_url=fake_api)
    with pytest.raises(et.TransportError, match="500"):
        t.chat("m-alpha", [{"role": "user", "content": "x"}])


def test_base_url_resolution_args_beat_env(clean_env, monkeypatch, fake_api):
    monkeypatch.setenv("EVAL_BASE_URL", "http://env.example/v1")
    assert et.resolve_base_url() == "http://env.example/v1"
    assert et.resolve_base_url("http://arg.example/v1") == "http://arg.example/v1"


def test_base_url_missing_everywhere_is_an_error(clean_env):
    with pytest.raises(et.TransportError, match="no endpoint"):
        et.resolve_base_url()


def test_extract_text_handles_choices_shape():
    payload = {"choices": [{"message": {"content": "  hi there  "}}]}
    assert et.extract_text(payload) == "hi there"
    assert et.extract_text({"choices": []}) == ""


# ---- parse-rate guardrail ----------------------------------------------------


def test_chat_json_counts_samples_and_parsed(fake_api):
    t = et.EvalTransport(base_url=fake_api)
    msg = [{"role": "user", "content": "give json"}]
    t.chat_json("m-alpha", msg)
    t.chat_json("m-alpha", msg)
    parsed, samples = t.parse_stats()
    assert (parsed, samples) == (2, 2)


def test_parse_rate_guardrail_raises_named_exception(fake_api):
    _FakeAPI.content_factory = lambda body: "this is prose, not JSON {"
    t = et.EvalTransport(base_url=fake_api, parse_floor=0.6)
    msg = [{"role": "user", "content": "give json"}]
    with pytest.raises(et.ParseRateError) as excinfo:
        for _ in range(8):  # floor enforced once min_samples reached
            try:
                t.chat_json("m-alpha", msg)
            except et.ReplyParseError:
                continue  # a MISS is recorded; the sweep keeps going
            pytest.fail("expected every reply to be unparseable")
    assert "parse rate" in str(excinfo.value).lower()
    parsed, samples = t.parse_stats()
    assert samples >= t.min_samples and parsed / samples < 0.6


def test_single_parse_failure_below_min_samples_does_not_raise(fake_api):
    """One fluke must not kill a run whose rate could still recover."""
    calls = {"n": 0}

    def mostly_json(body):
        calls["n"] += 1
        return '{"ok": true}' if calls["n"] > 1 else "one-off garbage"

    _FakeAPI.content_factory = mostly_json
    t = et.EvalTransport(base_url=fake_api)  # defaults: floor .6, min 5
    msg = [{"role": "user", "content": "give json"}]
    for _ in range(5):
        try:
            t.chat_json("m-alpha", msg)
        except et.ReplyParseError:
            pass  # recorded as a MISS; rate stays above floor: no raise
    assert t.parse_stats() == (4, 5)


def test_parse_rate_guardrail_not_triggered_when_floor_met(fake_api):
    _FakeAPI.content_factory = lambda body: (
        '{"ok": true}' if body["messages"][0]["content"] != "bad" else "nope"
    )
    t = et.EvalTransport(base_url=fake_api, parse_floor=0.6)
    msg_ok = [{"role": "user", "content": "good"}]
    msg_bad = [{"role": "user", "content": "bad"}]
    for _ in range(4):
        t.chat_json("m-alpha", msg_ok)
    try:
        t.chat_json("m-alpha", msg_bad)  # 4/5 = 0.8 above floor: no raise
    except et.ReplyParseError:
        pass
    parsed, samples = t.parse_stats()
    assert (parsed, samples) == (4, 5)


# ---- resumable sweep state ---------------------------------------------------


def _mk_state(tmp_path):
    return et.SweepState(tmp_path / "sweep_state.json")


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
        "m-alpha", ["t1", "t2"],
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
        "m-alpha", ["t1"],
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
        "m1", ["t1", "t2"],
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
    st.start([f"w{i}" for i in range(workers)],
             [f"t{j:03d}" for j in range(per_worker)])

    script = "\n".join([
        "import sys",
        f"sys.path.insert(0, {str(REPO_ROOT)!r})",
        "from lib.eval_transport import SweepState",
        f"st = SweepState({str(path)!r})",
        "wid = sys.argv[1]",
        f"for j in range({per_worker}):",
        "    st.mark_done(f'w{wid}', f't{j:03d}')",
    ])
    procs = [
        sp.Popen([sys.executable, "-c", script, str(i)],
                 cwd=str(tmp_path),
                 stdout=sp.PIPE, stderr=sp.PIPE, text=True)
        for i in range(workers)
    ]
    for p in procs:
        out, errout = p.communicate(timeout=120)
        assert p.returncode == 0, f"worker died: {errout}"

    fresh = et.SweepState(path)  # a NEW process reads what landed on disk
    s = fresh.summary()
    assert s["done"] == workers * per_worker, (
        f"lost updates: {s['done']}/{workers * per_worker} markers survived")
    assert fresh.resume([f"w{i}" for i in range(workers)],
                        [f"t{j:03d}" for j in range(per_worker)]) == []


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


# ── whole-call deadline: slow-drip bodies cannot stretch a call ──────────────


class _DripHandler(BaseHTTPRequestHandler):
    """Sends headers fast, then drips the body forever in tiny pieces with
    sleeps SHORTER than any sane per-op socket timeout — the shape that used
    to stretch one chat() call from a 1s timeout to 15.6s."""

    interval = 0.25

    def log_message(self, *a):  # silence the test runner
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(10 ** 8))
        self.end_headers()
        try:
            while True:
                self.wfile.write(b"x" * 32)
                time.sleep(_DripHandler.interval)
        except OSError:
            pass  # client gave up — expected under the deadline


def test_whole_call_deadline_bounds_a_slow_drip_body(fake_api=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DripHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        t = et.EvalTransport(base_url=base, timeout=2.0)
        start = time.monotonic()
        with pytest.raises(et.TransportError, match="deadline"):
            t.chat("m", [{"role": "user", "content": "hello"}])
        elapsed = time.monotonic() - start
        # Bounded by the whole-call deadline (+ small tolerance), NOT by the
        # never-ending drip: pre-fix behavior was unbounded growth.
        assert elapsed < 6.0, f"call stretched {elapsed:.1f}s past a 2s timeout"
    finally:
        server.shutdown()
        server.server_close()


def test_normal_response_still_parses_after_chunked_reads(fake_api):
    # Calibration control: the chunked-deadline read path must not break the
    # ordinary small-body case.
    t = et.EvalTransport(base_url=fake_api, api_key="k")
    out = t.chat("m-alpha", [{"role": "user", "content": "hi"}])
    assert out["text"] == json.dumps({"echo": "m-alpha"})
