"""eval_transport — grader/model-agnostic eval transport (stdlib only).

Posture inherited from the ancestors:
  - CadGoose eval_goose_ai.py: OpenAI-compatible /chat/completions round-trip,
    base URL + API key from args/env, response-text extraction.
  - ztools benchmarks/sweep_models.sh: resumable sweeps with DONE markers, and
    the hard lesson that an all-zero / truncated run must LOOK truncated —
    planned vs done counts are recorded so resume() can tell the difference.
  - ZeroThunder grader_eval/grader_eval.py: parse-rate guardrail — too few
    machine-readable replies is BROKEN (a transport/parse problem), not a
    verdict. Crossing the floor raises ParseRateError instead of letting a
    broken pipeline average garbage into "results".

No provider SDK: urllib from the standard library only.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 120.0

# grader_eval's MIN_PARSE_RATE posture: below this fraction of readable replies
# the instrument is BROKEN and its output must not be trusted as results.
DEFAULT_PARSE_FLOOR = 0.6
# ...judged over a BATCH, not the first reply: one fluke parse failure must not
# kill a run whose rate could still recover above the floor.
DEFAULT_MIN_SAMPLES = 5

STATE_VERSION = 1


class TransportError(RuntimeError):
    """HTTP or network failure against the eval endpoint."""


class ReplyParseError(RuntimeError):
    """One reply was not machine-readable — a MISS (grader_eval rule: a
    raised call or empty parse is recorded, never an aborted probe)."""

    def __init__(self, text: str):
        self.text = text
        super().__init__(f"reply not JSON: {text[:120]!r}")


class ParseRateError(RuntimeError):
    """More than (1 - floor) of replies were unparseable — the pipeline is
    BROKEN; fix the transport/parser, never score through it."""

    def __init__(self, parsed: int, samples: int, floor: float):
        self.parsed = parsed
        self.samples = samples
        self.floor = floor
        rate = parsed / samples if samples else 0.0
        super().__init__(
            f"parse rate {parsed}/{samples} ({rate:.2f}) fell below floor "
            f"{floor:.2f} — BROKEN transport/parse, not a result"
        )


# ─── endpoint resolution ──────────────────────────────────────────────────────


def resolve_base_url(base_url: str | None = None) -> str:
    """Explicit argument wins, then EVAL_BASE_URL, then OPENAI_BASE_URL."""
    url = base_url or os.environ.get("EVAL_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    if not url:
        raise TransportError(
            "no endpoint: pass base_url or set EVAL_BASE_URL / OPENAI_BASE_URL"
        )
    return url.rstrip("/")


def resolve_api_key(api_key: str | None = None) -> str | None:
    return api_key or os.environ.get("EVAL_API_KEY") or os.environ.get("OPENAI_API_KEY")


def extract_text(response_payload: dict) -> str:
    """Pull choices[0].message.content out of a chat-completion response."""
    try:
        return (response_payload.get("choices") or [{}])[0].get("message", {}).get(
            "content", ""
        ).strip()
    except (AttributeError, IndexError, TypeError):
        return ""


# ─── transport ────────────────────────────────────────────────────────────────


class EvalTransport:
    """Chat-completion round-trips against one OpenAI-compatible endpoint,
    with a running JSON-parse-rate guardrail across chat_json calls."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        parse_floor: float = DEFAULT_PARSE_FLOOR,
        min_samples: int = DEFAULT_MIN_SAMPLES,
    ):
        self.base_url = resolve_base_url(base_url)
        self.api_key = resolve_api_key(api_key)
        self.timeout = timeout
        self.parse_floor = parse_floor
        self.min_samples = max(int(min_samples), 2)
        self._parsed = 0
        self._samples = 0

    # -- raw HTTP ---------------------------------------------------------

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:200]
            raise TransportError(f"HTTP {exc.code} from {url}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransportError(f"cannot reach {url}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise TransportError(f"non-JSON body from {url}") from exc

    # -- endpoint discovery -------------------------------------------------

    def discover_models(self) -> list[str]:
        """GET /models → sorted model ids. Raises TransportError when down."""
        payload = self._request("GET", "/models")
        ids = []
        for entry in payload.get("data", []):
            name = entry.get("id") if isinstance(entry, dict) else entry
            if name:
                ids.append(name)
        return sorted(ids)

    # -- chat ---------------------------------------------------------------

    def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> dict:
        """One completion round-trip → {text, elapsed, tokens}. Raises
        TransportError on HTTP/network failure."""
        body: dict = {"model": model, "messages": messages, "temperature": temperature}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        start = time.monotonic()
        payload = self._request("POST", "/chat/completions", body)
        elapsed = time.monotonic() - start
        usage = payload.get("usage", {}) or {}
        return {
            "text": extract_text(payload),
            "elapsed": round(elapsed, 3),
            "tokens": usage.get("completion_tokens", 0),
        }

    def chat_json(self, model: str, messages: list[dict], **chat_kwargs) -> object:
        """chat(), then parse the reply text as JSON.

        Every HTTP-successful call counts toward the guardrail: an unparseable
        reply raises ReplyParseError after being counted as a MISS; once
        min_samples have accrued, a rate below parse_floor escalates to
        ParseRateError (the grader_eval BROKEN rule)."""
        reply = self.chat(model, messages, **chat_kwargs)
        self._samples += 1
        try:
            value = json.loads(reply["text"])
        except json.JSONDecodeError:
            self._check_floor()  # may escalate to ParseRateError
            raise ReplyParseError(reply["text"]) from None
        self._parsed += 1
        self._check_floor()
        return value

    def parse_stats(self) -> tuple[int, int]:
        """(parsed, samples) so callers can report the instrument's health."""
        return self._parsed, self._samples

    def _check_floor(self) -> None:
        if (
            self._samples >= self.min_samples
            and self._parsed / self._samples < self.parse_floor
        ):
            raise ParseRateError(self._parsed, self._samples, self.parse_floor)


# ─── resumable sweep state ───────────────────────────────────────────────────


class SweepState:
    """Per-(model, task) DONE-marker file for resumable sweeps.

    Atomicity contract: every write goes to a temp file in the same directory,
    fsynced, then os.replace()d over the target — readers (including a fresh
    process after a crash) see either the complete previous state or the
    complete new state, never a torn file.

    Truncation contract: `start()` records the full planned unit list once;
    summary() reports done/planned so a sweep killed mid-run reads as
    TRUNCATED, never silently complete.
    """

    def __init__(self, path: str | os.PathLike):
        self.path = os.fspath(path)

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return {"version": STATE_VERSION, "planned": [], "done": {}, "errors": {}}

    def _write(self, state: dict) -> None:
        directory = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".sweep-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _key(model: str, task: str) -> str:
        return f"{model}|{task}"

    def start(self, models: list[str], tasks: list[str]) -> None:
        """Record the planned unit grid once (first call wins — later starts
        on an existing plan must not shrink what 'complete' means)."""
        state = self._read()
        if state.get("planned"):
            return
        state["planned"] = [self._key(m, t) for m in models for t in tasks]
        self._write(state)

    def resume(self, models: list[str], tasks: list[str]) -> list[tuple[str, str]]:
        """Units from the (model x tasks) grid that have no DONE marker yet."""
        state = self._read()
        done = state.get("done", {})
        return [
            (m, t)
            for m in models
            for t in tasks
            if self._key(m, t) not in done
        ]

    def mark_done(self, model: str, task: str, note: str = "") -> None:
        state = self._read()
        state.setdefault("done", {})[self._key(model, task)] = {
            "status": "done",
            "note": note,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        state.get("errors", {}).pop(self._key(model, task), None)
        self._write(state)

    def record_error(self, model: str, task: str, error: str) -> None:
        state = self._read()
        state.setdefault("errors", {})[self._key(model, task)] = {
            "error": str(error)[:500],
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._write(state)

    def summary(self) -> dict:
        state = self._read()
        planned = len(state.get("planned", []))
        done = len(state.get("done", {}))
        return {
            "planned": planned,
            "done": done,
            "errors": len(state.get("errors", {})),
            "complete": bool(planned) and done >= planned,
            # A truncated run must be visible: fewer done than planned.
            "truncated": bool(planned) and done < planned,
        }


def run_loop(model: str, tasks: list, unit_fn, state: SweepState) -> dict:
    """Skeleton resumable loop over one model's tasks.

    unit_fn(model, task) -> note (any JSON-serializable). Skips units already
    marked done; marks each unit done atomically right after it succeeds; a
    unit that raises is recorded as an error and stays pending for the next
    resume. Returns the end-of-run summary (check .truncated before trusting
    results).
    """
    pending = state.resume([model], list(tasks))
    for _, task in pending:
        try:
            note = unit_fn(model, task)
        except Exception as exc:  # noqa: BLE001 — record, keep sweeping
            state.record_error(model, task, repr(exc))
            continue
        state.mark_done(model, task, note=str(note))
    return state.summary()
