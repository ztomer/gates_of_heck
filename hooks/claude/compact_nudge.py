#!/usr/bin/env python3
"""A Claude Code Stop hook: say when a turn ends with the context past a size worth compacting.

Claude Code compacts on its own only at its token threshold, mid-task as often as not, and nothing
a hook, a setting or the model can do starts a compaction. The end of a turn is the boundary, so
this hook speaks there: once the session's context passes `GOH_COMPACT_NUDGE_AT` tokens it shows a
`systemMessage` naming the size and `/compact`, then stays quiet until the context grows another
`GOH_COMPACT_NUDGE_STEP`. A compaction re-arms it. It never blocks the stop and never fails one:
an unreadable event or transcript is silence (tests/test_claude_compact_hooks.py).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

DEFAULT_AT = 300_000
DEFAULT_STEP = 50_000
CHUNK = 1 << 16
STATE_TTL_S = 7 * 24 * 3600
KILO = 1000


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _lines_backwards(path: Path):
    """Each line of the file, last first, without reading the whole of a multi-megabyte transcript."""
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        tail = b""
        while pos > 0:
            step = min(CHUNK, pos)
            pos -= step
            f.seek(pos)
            parts = (f.read(step) + tail).split(b"\n")
            tail = parts[0]
            for line in reversed(parts[1:]):
                if line.strip():
                    yield line
        if tail.strip():
            yield tail


def context_tokens(transcript: Path) -> int | None:
    """The main conversation's context at its last API call; the post-compaction size when a
    compaction is newer than that call; None when the transcript holds neither."""
    for raw in _lines_backwards(transcript):
        if b'"usage"' not in raw and b"compact_boundary" not in raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if entry.get("subtype") == "compact_boundary":
            return int((entry.get("compactMetadata") or {}).get("postTokens") or 0)
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        usage = (entry.get("message") or {}).get("usage") or {}
        return sum(
            int(usage.get(k) or 0)
            for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        )
    return None


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "goh" / "compact_nudge"


def _prune(state: Path) -> None:
    cutoff = time.time() - STATE_TTL_S
    for f in state.glob("*"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def decide(ctx: int, last: int, at: int, step: int) -> bool:
    """Speak on the first crossing of a cycle, and again only after `step` more growth."""
    return ctx >= at and (last == 0 or ctx < last or ctx - last >= step)


def main() -> int:
    try:
        event = json.load(sys.stdin)
        transcript = Path(event["transcript_path"])
        session = "".join(c for c in str(event.get("session_id", "")) if c.isalnum() or c in "-_")
    except (ValueError, KeyError, TypeError):
        return 0
    try:
        ctx = context_tokens(transcript)
    except OSError:
        return 0
    if ctx is None or not session:
        return 0
    at, step = (
        _int_env("GOH_COMPACT_NUDGE_AT", DEFAULT_AT),
        _int_env("GOH_COMPACT_NUDGE_STEP", DEFAULT_STEP),
    )
    state = _state_dir()
    marker = state / session
    try:
        last = int(marker.read_text().strip() or 0)
    except (OSError, ValueError):
        last = 0
    if ctx < at:
        marker.unlink(missing_ok=True)
        return 0
    if not decide(ctx, last, at, step):
        return 0
    try:
        state.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(ctx))
        _prune(state)
    except OSError:
        pass
    message = (
        f"→ context at {ctx // KILO}k tokens. If this task is done, /compact now puts the "
        "summary on a task boundary instead of wherever the automatic threshold lands."
    )
    print(json.dumps({"systemMessage": message}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
