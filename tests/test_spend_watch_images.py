"""scripts/spend_watch.py: image results and measured baselines (GOH.IMGREAD, GOH.READBASE).

Split from tests/test_spend_watch.py (the file cap), whose transcript builders these reuse: every
number below is worked out by hand.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from test_spend_watch import (
    NOW,
    Loopctl,
    _agent,
    _at,
    _landed,
    _resp,
    _result,
    _run,
    _tool,
    _usage,
    _worker,
    sw,
)

# --- image results: costed in tokens from their pixels, never their bytes (GOH.IMGREAD) -----------


def _png(w: int, h: int, pad: int = 0) -> str:
    """A base64 PNG header for a w x h image, padded to `pad` extra payload bytes."""
    import base64
    import struct

    raw = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", w, h)
        + b"\x08\x06\x00\x00\x00"
    )
    return base64.b64encode(raw + b"\x00" * pad).decode()


def _image_result(at: str, uid: str, data: str, kind: str = "image/png") -> dict[str, Any]:
    block = {"type": "image", "source": {"type": "base64", "media_type": kind, "data": data}}
    result = {"type": "tool_result", "tool_use_id": uid, "content": [block]}
    return {"type": "user", "timestamp": at, "message": {"content": [result]}}


def test_an_image_result_costs_its_pixels_over_750_not_its_bytes(tmp_path: Path) -> None:
    """Two screenshots of one size, one carrying 300 KB more payload: the same tokens, by tool.
    Anthropic's vision docs: tokens = width * height / 750 (an 800x600 image is 640 tokens)."""
    shot = "mcp__Claude_Browser__browser_batch"
    recs = [*_resp("m1", _at(1), _usage(9000, w5=1000, out=100), _tool("s", shot)),
            _image_result(_at(1, 0.1), "s", _png(800, 600)),
            *_resp("m2", _at(1, 1), _usage(10000, w5=1000), _tool("r", "Read", file_path="/s.png")),
            _image_result(_at(1, 1.1), "r", _png(800, 600, pad=300_000)),
            *_resp("m3", _at(1, 2), _usage(11000, w5=1000), _tool("t", "Read", file_path="/a.py")),
            _result(_at(1, 2.1), "t", "x" * 400),
            *_resp("m4", _at(1, 3), _usage(12000, out=100))]  # fmt: skip
    _agent(tmp_path, "img", "general-purpose", recs)
    _worker(tmp_path, "plain", 2, first=30000)
    m = sw.week(sw.agents(tmp_path, NOW - dt.timedelta(days=14)), NOW - dt.timedelta(days=7), NOW)
    assert m["image_results"] == {
        "Read": {"count": 1, "tokens": 640},
        shot: {"count": 1, "tokens": 640},
    }
    assert m["image_tokens_per_agent"] == 640.0  # 1280 image tokens over 2 agents
    # an image Read is no Read text: the text share stays 400 + 300 of 400 + 300 + 4 * 100
    assert m["read_bytes"] == 700 and m["read_share"] == round(700 / 1100, 4)


def test_an_unreadable_image_is_counted_and_never_guessed(tmp_path: Path) -> None:
    recs = [*_resp("m1", _at(1), _usage(9000, out=100), _tool("s", "Read", file_path="/x.gif")),
            _image_result(_at(1, 0.1), "s", "R0lGODlh", kind="image/gif"),
            *_resp("m2", _at(1, 1), _usage(9000, out=100))]  # fmt: skip
    _agent(tmp_path, "gif", "general-purpose", recs)
    m = sw.week(sw.agents(tmp_path, NOW - dt.timedelta(days=14)), NOW - dt.timedelta(days=7), NOW)
    assert m["image_results"] == {"Read": {"count": 1, "tokens": 0, "unsized": 1}}


def test_image_tokens_per_agent_is_a_judged_metric() -> None:
    assert sw.METRICS["image_tokens_per_agent"]["label"] == "image result tokens per agent"


# --- a prediction's baseline names the instrument that took it (GOH.READBASE) --------------------


def test_the_read_share_reads_back_a_planted_share(tmp_path: Path) -> None:
    """Calibration: three agents whose text Reads are planted at exactly 25% of their text results
    (100 of 400 per agent), each also Reading a large screenshot: the reader gives 0.25, so an image
    is never Read text (an images-as-bytes reader gives over 99% here)."""
    for i in range(3):
        recs = [*_resp(f"m{i}a", _at(1), _usage(9000), _tool(f"r{i}", "Read", file_path="/a")),
                _result(_at(1, 0.1), f"r{i}", "x" * 100),
                *_resp(f"m{i}b", _at(1, 1), _usage(9000), _tool(f"b{i}", "Bash", command="ls")),
                _result(_at(1, 1.1), f"b{i}", "y" * 300),
                *_resp(f"m{i}c", _at(1, 2), _usage(9000), _tool(f"s{i}", "Read", file_path="/s")),
                _image_result(_at(1, 2.1), f"s{i}", _png(1568, 1000, pad=250_000)),
                *_resp(f"m{i}d", _at(1, 3), _usage(9000))]  # fmt: skip
        _agent(tmp_path, f"p{i}", "general-purpose", recs)
    m = sw.week(sw.agents(tmp_path, NOW - dt.timedelta(days=14)), NOW - dt.timedelta(days=7), NOW)
    assert m["read_share"] == 0.25 and m["read_bytes"] == 300


def test_a_measured_baseline_outranks_the_prediction_text(tmp_path: Path) -> None:
    """READCAP's '42% -> under 15%' was predicted against a hand count that took screenshots as
    bytes; its record now carries the baseline the current reader re-derived. The check uses that,
    names where it came from, and re-marks an item whose recorded check used the old baseline."""
    p = tmp_path / "projects"
    for i in range(3):
        _worker(p, f"n{i}", 1 + i * 0.1, first=30000, read_text="x" * 100)  # read share 100/500
    label = sw.METRICS["read_share"]["label"]
    named = _landed("R", label, "42% -> under 15%", 9) | {
        "baseline_measured": {
            "value": 0.043,
            "instrument": "spend_watch read_share",
            "window": "W",
        },
        "spend_check": {"verdict": "confirmed", "measured": 0.2, "baseline": 0.42, "target": 0.15},
    }
    bare = _landed("T", label, "42% -> under 30%", 9)
    lc = Loopctl(tmp_path)
    out = _run(tmp_path, [named, bare], loopctl=lc.path)
    v = {c["id"]: c for c in out["predictions"]}
    assert v["R"]["baseline"] == 0.043 and v["R"]["verdict"] == "regressed"
    assert v["R"]["baseline_from"] == "spend_watch read_share over W"
    assert v["T"]["baseline"] == 0.42 and v["T"]["verdict"] == "confirmed"
    assert v["T"]["baseline_from"] == "the `predicted` text: no instrument named"
    sets = {c[1]: json.loads(c[3]) for c in lc.calls() if c[0] == "set"}
    assert sets["R"]["baseline"] == 0.043 and sets["R"]["baseline_from"].startswith("spend_watch")
