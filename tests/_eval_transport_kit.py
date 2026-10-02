"""Shared plumbing for the two eval_transport suites.

HTTP is exercised against a REAL local server (`http.server` on a thread), never a mock of the
transport itself, and both suites need that endpoint: one drives the chat round trip over it, the
other drives the sweep loop that calls it. The fake endpoint, its rewind-between-tests, and the
state-file helper live here so the two files share one definition of "the endpoint" — a second copy
would be free to answer differently, and the difference would look like a behaviour change in
eval_transport rather than in the fixture.
"""

import json
import sys
import threading
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
        self._send_json(
            {
                "id": "chatcmpl-x",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
                "usage": {"completion_tokens": len(content)},
            }
        )


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


def _mk_state(tmp_path):
    return et.SweepState(tmp_path / "sweep_state.json")
