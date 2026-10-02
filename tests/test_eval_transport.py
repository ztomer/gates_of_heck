"""eval_transport — chat-completion round-trip, parse-rate guardrail,
resumable sweep state. HTTP is exercised against a REAL local server
(http.server thread), not a mock of the transport itself.

The resumable half of that promise — the state file and the loop that drives it, read back by a NEW
process after a simulated crash — is in `test_eval_transport_sweep.py`. What stays here is the
transport itself: one call out, one call back, and the two guards that stand between a bad reply
and a silently bad sweep (the parse-rate floor, and the whole-call deadline a slow-drip body cannot
stretch). The fake endpoint is shared with that file through `_eval_transport_kit`, so both suites
are talking to the same server definition.
"""

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from _eval_transport_kit import (
    _FakeAPI,
    _rewire,  # noqa: F401  (autouse: rewinds the fake endpoint between tests)
    clean_env,  # noqa: F401
    et,
    fake_api,  # noqa: F401
)

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
        self.send_header("Content-Length", str(10**8))
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
