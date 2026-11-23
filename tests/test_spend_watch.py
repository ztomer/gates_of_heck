"""scripts/spend_watch.py: the weekly watch's SPEND section (GOH.SPENDWATCH).

Synthetic subagent transcripts in the shape Claude Code 2.1.311 writes: an `assistant` API response
split over one record per content block, each repeating the same `message.id` and `usage` (so the
usage counts ONCE), `user` records with `tool_result` blocks, and `agent-<id>.meta.json` beside the
transcript carrying `agentType`. Every number below is worked out by hand.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT, git

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import spend_watch as sw

NOW = dt.datetime(2026, 11, 9, 9, 30, tzinfo=dt.UTC)


def _at(days_ago: float, minutes: float = 0) -> str:
    return (NOW - dt.timedelta(days=days_ago) + dt.timedelta(minutes=minutes)).isoformat()


def _usage(read: int, w5: int = 0, w1: int = 0, out: int = 0, inp: int = 0) -> dict[str, Any]:
    return {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": w5 + w1,
            "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1}}  # fmt: skip


def _resp(
    mid: str, at: str, usage: dict[str, Any], *blocks: dict[str, Any]
) -> list[dict[str, Any]]:
    """One API response, one record per content block, usage repeated on each (as Claude Code writes)."""
    blocks = blocks or ({"type": "text", "text": "done"},)
    return [{"type": "assistant", "timestamp": at,
             "message": {"id": mid, "content": [b], "usage": usage}} for b in blocks]  # fmt: skip


def _bash(uid: str, cmd: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": uid, "name": "Bash", "input": {"command": cmd}}


def _tool(uid: str, name: str, **inp: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": uid, "name": name, "input": inp}


def _result(at: str, uid: str, text: str) -> dict[str, Any]:
    return {"type": "user", "timestamp": at,
            "message": {"content": [{"type": "tool_result", "tool_use_id": uid, "content": text}]}}  # fmt: skip


def _agent(root: Path, name: str, kind: str, recs: list[dict[str, Any]]) -> None:
    d = root / "-proj" / "sess" / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"agent-{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    (d / f"agent-{name}.meta.json").write_text(json.dumps({"agentType": kind}))


def _worker(root: Path, name: str, days_ago: float, first: int, *, push: bool = True,
            gap: bool = False, read_text: str = "x" * 300) -> None:  # fmt: skip
    """A write agent: read, edit, test, push, poll, report. Context per turn = first, +1000 each."""
    t = lambda m: _at(days_ago, m)
    recs = [*_resp(f"{name}1", t(0), _usage(first - 1000, w5=1000, out=100), _tool(f"{name}r", "Read", file_path="/a")),
            _result(t(0.1), f"{name}r", read_text),
            *_resp(f"{name}2", t(1), _usage(first, w5=1000, out=100), _tool(f"{name}e", "Edit", file_path="/a")),
            _result(t(1.1), f"{name}e", "y" * 100),
            *_resp(f"{name}3", t(2), _usage(first + 1000, w5=1000, out=100), _bash(f"{name}t", "tools/pytest.sh -q")),
            _result(t(2.1), f"{name}t", "z" * 100),
            *_resp(f"{name}4", t(10 if gap else 3), _usage(first + 2000, w5=1000, out=100),
                   _bash(f"{name}p", "git push origin main" if push else "git status")),
            _result(t(10.1 if gap else 3.1), f"{name}p", "w" * 100),
            *_resp(f"{name}5", t(11 if gap else 4), _usage(first + 3000, w5=1000, out=100), _bash(f"{name}g", "gh run watch 1")),
            _result(t(11.1 if gap else 4.1), f"{name}g", "v" * 100),
            *_resp(f"{name}6", t(12 if gap else 5), _usage(first + 4000, w5=1000, out=100))]  # fmt: skip
    _agent(root, name, "repo-worker", recs)


# --- the measurements, each against a hand-worked number -----------------------------------------


def test_one_api_response_split_over_records_counts_once(tmp_path: Path) -> None:
    """Three records of one message id: its usage counts once, its three tool calls all count."""
    recs = _resp("m1", _at(1), _usage(9000, w5=1000, out=200),
                 _tool("a", "Read", file_path="/x"), _tool("b", "Read", file_path="/y"),
                 _tool("c", "Read", file_path="/z"))  # fmt: skip
    _agent(tmp_path, "one", "general-purpose", recs)
    m = sw.week(sw.agents(tmp_path, NOW - dt.timedelta(days=14)), NOW - dt.timedelta(days=7), NOW)
    assert m["turns"] == 1 and m["calls"] == 3
    # cost units: read 9000*0.1=900, write 1000*1.25=1250, output 200*5=1000 -> 3150
    assert m["cost_units"] == 3150
    assert m["cost_share"] == {"read": round(900 / 3150, 4), "write": round(1250 / 3150, 4),
                               "output": round(1000 / 3150, 4), "input": 0.0}  # fmt: skip


def test_a_record_written_twice_counts_once(tmp_path: Path) -> None:
    """A resumed transcript can repeat a record line: its usage and its tool call count once."""
    recs = _resp("m1", _at(1), _usage(9000), _bash("a", "git status"))
    _agent(tmp_path, "dup", "repo-worker", recs + recs)
    m = sw.week(sw.agents(tmp_path, NOW - dt.timedelta(days=14)), NOW - dt.timedelta(days=7), NOW)
    assert (m["turns"], m["calls"], m["repeated_share"], m["cost_units"]) == (1, 1, 0.0, 900)


def test_the_week_metrics_of_two_workers(tmp_path: Path) -> None:
    _worker(tmp_path, "a", 1, first=30000)
    _worker(tmp_path, "b", 2, first=40000, gap=True, read_text="x" * 900)
    m = sw.week(sw.agents(tmp_path, NOW - dt.timedelta(days=14)), NOW - dt.timedelta(days=7), NOW)
    assert m["agents"] == 2 and m["turns_per_agent"] == 6
    assert m["first_turn"] == {"repo-worker": 35000} and m["first_turn_write"] == 35000
    # context per turn: a 30k..35k (sum 195k), from its push (turn 4) on 33k+34k+35k = 102k;
    # b 40k..45k (sum 255k), from push on 43k+44k+45k = 132k. (102+132)/(195+255) = 0.52
    assert m["post_push_share"] == round(234 / 450, 4)
    # post-push turns by kind: land 33k+43k, verify 34k+44k, wait/report 35k+45k over 450k
    assert m["post_push_by_kind"] == {"land/release": round(76 / 450, 4),
                                      "verify/poll/logs": round(78 / 450, 4),
                                      "wait/report": round(80 / 450, 4)}  # fmt: skip
    # one idle gap (b's push turn, 8 min after its test turn): 1000 5m-written tokens = 1250 units
    total = sum(
        sum((f - 1000 + k * 1000) * 0.1 + 1250 + 500 for k in range(6)) for f in (30000, 40000)
    )
    assert m["cost_units"] == round(total) and m["gap_write_share"] == round(1250 / total, 4)
    # Read bytes 300 + 900 of 300+900 + 2*(4*100) tool-result bytes
    assert m["read_bytes"] == 1200 and m["read_share"] == round(1200 / 2000, 4)
    assert m["calls"] == 10 and m["repeated_share"] == 0.0  # five distinct calls each


def test_repeated_calls_are_counted_within_one_agent(tmp_path: Path) -> None:
    recs = [*_resp("m1", _at(1), _usage(100), _bash("a", "git status")),
            _result(_at(1, 1), "a", "clean"),
            *_resp("m2", _at(1, 2), _usage(100), _bash("b", "git status")),
            _result(_at(1, 3), "b", "clean"),
            *_resp("m3", _at(1, 4), _usage(100), _bash("c", "git log -1"))]  # fmt: skip
    _agent(tmp_path, "r", "repo-worker", recs)
    _worker(tmp_path, "w", 1, first=30000)  # five distinct calls
    m = sw.week(sw.agents(tmp_path, NOW - dt.timedelta(days=14)), NOW - dt.timedelta(days=7), NOW)
    assert m["calls"] == 8 and m["repeated_share"] == round(1 / 8, 4)


# --- weekly windows, deltas and the regression filing --------------------------------------------


class Loopctl:
    """A fake loopctl.py: records each call, refuses a `file` whose id it already holds."""

    def __init__(self, tmp: Path) -> None:
        self.path = tmp / "loopctl.py"
        self.log = tmp / "loopctl.log"
        self.path.write_text(
            "import json, sys, pathlib\n"
            f"log = pathlib.Path({str(self.log)!r})\n"
            "calls = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []\n"
            "argv = sys.argv[1:]\n"
            "assert argv[:1] == ['--queue'], argv  # every edit names the queue the watch read\n"
            f"assert argv[1] == {str(tmp / 'queue.json')!r}, argv\n"
            "argv = argv[2:]\n"
            "if argv[0] == 'file' and any(c[0] == 'file' and json.loads(c[2])['id'] == json.loads(argv[2])['id'] for c in calls):\n"
            "    print('ERR ' + json.loads(argv[2])['id'] + ' already exists; `set` edits an item'); sys.exit(1)\n"
            "with log.open('a') as f: f.write(json.dumps(argv) + '\\n')\n"
        )

    def calls(self) -> list[list[str]]:
        return (
            [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []
        )

    def filed(self) -> list[dict[str, Any]]:
        return [json.loads(c[2]) for c in self.calls() if c[0] == "file"]


def _run(tmp: Path, items: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    queue = tmp / "queue.json"
    queue.write_text(json.dumps({"items": items}))
    claude_md = tmp / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text("x" * 7994)
    return sw.run(projects=tmp / "projects", now=NOW, queue=queue, loopctl=kw.pop("loopctl", None),
                  ccusage=kw.pop("ccusage", None), claude_md=claude_md,
                  prior=kw.pop("prior", {}), repos=tmp, **kw)  # fmt: skip


def test_a_metric_worse_than_last_week_files_one_queue_item(tmp_path: Path) -> None:
    """Planted regression: last week's workers started at 30k, this week's at 60k."""
    p = tmp_path / "projects"
    for i in range(3):
        _worker(p, f"old{i}", 9 + i * 0.1, first=30000)
        _worker(p, f"new{i}", 1 + i * 0.1, first=60000)
    lc = Loopctl(tmp_path)
    out = _run(tmp_path, [], loopctl=lc.path)
    first = out["delta"]["first_turn_write"]
    assert (first["prev"], first["now"], first["worse"]) == (30000, 60000, True)
    filed = lc.filed()
    assert [f["id"] for f in filed] == [f"SPEND.WORSE.FIRST_TURN_WRITE.{NOW:%G-W%V}"]
    item = filed[0]
    assert item["phase"] == "EC" and item["status"] == "todo" and item["repo"] == "gates_of_heck"
    assert item["metric"] == sw.METRICS["first_turn_write"]["label"] and item["owner_words"]
    assert "30000" in item["item"] and "60000" in item["item"] and len(item["done_state"]) >= 40
    # the same week again: refused as already filed, reported as such, never a second item
    again = _run(tmp_path, [], loopctl=lc.path)
    assert len(lc.filed()) == 1 and again["filed"][0]["outcome"] == "already filed"


def test_filing_never_reaches_a_queue_the_watch_did_not_read(tmp_path: Path) -> None:
    """2026-11-08: a run reading a scratch queue filed into the LIVE one (loopctl's default)."""
    for i in range(3):
        _worker(tmp_path / "projects", f"old{i}", 9 + i * 0.1, first=30000)
        _worker(tmp_path / "projects", f"new{i}", 1 + i * 0.1, first=60000)
    lc = Loopctl(tmp_path)
    assert (
        _run(tmp_path, [], loopctl=lc.path)["filed"][0]["outcome"] == "filed"
    )  # the fake asserts --queue
    out = sw.run(projects=tmp_path / "projects", now=NOW, queue=None, loopctl=lc.path, ccusage=None,
                 claude_md=tmp_path / "CLAUDE.md", prior={}, repos=tmp_path)  # fmt: skip
    assert out["filed"] and all(
        f["outcome"].startswith("not filed: no queue") for f in out["filed"]
    )


def test_no_filing_with_too_few_agents(tmp_path: Path) -> None:
    _worker(tmp_path / "projects", "old", 9, first=30000)
    _worker(
        tmp_path / "projects", "new", 1, first=60000
    )  # worse, but one agent a week is no verdict
    lc = Loopctl(tmp_path)
    out = _run(tmp_path, [], loopctl=lc.path)
    assert out["delta"]["first_turn_write"]["worse"] is None and lc.filed() == []


def test_no_filing_when_better(tmp_path: Path) -> None:
    for i in range(3):
        _worker(tmp_path / "projects", f"o{i}", 9 + i * 0.1, first=60000)
        _worker(tmp_path / "projects", f"n{i}", 1 + i * 0.1, first=30000)
    lc = Loopctl(tmp_path)
    out = _run(tmp_path, [], loopctl=lc.path)
    assert out["delta"]["first_turn_write"]["worse"] is False and lc.filed() == []


def test_the_rendered_section_shows_each_metric_with_its_delta(tmp_path: Path) -> None:
    p = tmp_path / "projects"
    for i in range(3):
        _worker(p, f"old{i}", 9 + i * 0.1, first=30000)
        _worker(p, f"new{i}", 1 + i * 0.1, first=60000)
    text = "\n".join(sw.render(_run(tmp_path, [])))
    assert "| median first-turn context of write agents | 30000 | 60000 | +30000 | worse |" in text
    assert "## Spend" in text and "post-push" in text


# --- predictions --------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "want"), [
    ("42% -> under 15%", (0.42, 0.15)),
    ("10% -> under 3%", (0.10, 0.03)),
    ("29.9 KB -> under 8 KB (about -5k tokens per agent turn)", (29900, 8000)),
    ("70.6k -> ~20-25k (about -20% of agent cost)", (70600, 25000)),
    ("49% -> under 25%", (0.49, 0.25)),
    ("the 2026-11-08 figures reproduced", None),
    (None, None),
])  # fmt: skip
def test_a_prediction_parses_to_baseline_and_target(text: str | None, want: Any) -> None:
    got = sw.parse_prediction(text)
    assert got == (pytest.approx(want[0]), pytest.approx(want[1])) if want else got is None


def _landed(item_id: str, metric: str, predicted: str, days_ago: float) -> dict[str, Any]:
    return {"id": item_id, "status": "done", "phase": "EC", "repo": "gates_of_heck",
            "metric": metric, "predicted": predicted, "landed_at": _at(days_ago)}  # fmt: skip


def test_a_landed_item_is_confirmed_short_or_regressed(tmp_path: Path) -> None:
    p = tmp_path / "projects"
    for i in range(3):
        _worker(p, f"n{i}", 1 + i * 0.1, first=30000, read_text="x" * 600)  # read share 600/1000
    label = sw.METRICS["read_share"]["label"]
    items = [_landed("A", label, "80% -> under 70%", 9), _landed("B", label, "70% -> under 50%", 9),
             _landed("C", label, "42% -> under 15%", 9), _landed("D", "something else", "1 -> 2", 9),
             _landed("E", label, "42% -> under 15%", 2)]  # fmt: skip
    lc = Loopctl(tmp_path)
    out = _run(tmp_path, items, loopctl=lc.path)
    v = {c["id"]: c for c in out["predictions"]}
    assert v["A"]["verdict"] == "confirmed" and v["B"]["verdict"] == "short"
    assert v["C"]["verdict"] == "regressed" and v["C"]["measured"] == 0.6
    assert v["D"]["verdict"] == "not measured" and "watch" in v["D"]["why"]
    assert v["E"]["provisional"]  # landed 2 days ago: shown, never marked or filed
    sets = {c[1]: json.loads(c[3]) for c in lc.calls() if c[0] == "set"}
    assert set(sets) == {"A", "B", "C"} and sets["C"]["verdict"] == "regressed"
    assert [f["id"] for f in lc.filed()] == ["SPEND.REGRESSED.C"]
    # the next run sees the same verdict already recorded: nothing set or filed again
    items[2]["spend_check"] = sets["C"]
    items[0]["spend_check"], items[1]["spend_check"] = sets["A"], sets["B"]
    n = len(lc.calls())
    _run(tmp_path, items, loopctl=lc.path)
    assert len(lc.calls()) == n


def test_a_file_metric_is_measured_now(tmp_path: Path) -> None:
    (tmp_path / "projects").mkdir()
    label = sw.METRICS["claude_md_bytes"]["label"]
    out = _run(tmp_path, [_landed("S", label, "29.9 KB -> under 8 KB", 9)])
    assert (
        out["predictions"][0]["measured"] == 7994
        and out["predictions"][0]["verdict"] == "confirmed"
    )


def test_a_landed_time_comes_from_the_first_resolvable_sha(tmp_path: Path, repo: Path) -> None:
    sha = git(repo, "rev-parse", "--short", "HEAD").strip()
    when = dt.datetime.fromisoformat(git(repo, "log", "-1", "--format=%cI").strip())
    item = {"id": "X", "repo": repo.name, "result": f"v0.1.0 deadbee {sha} landed"}
    assert sw.landed_at(item, repo.parent, {}, NOW) == when
    seen: dict[str, str] = {}
    assert sw.landed_at({"id": "Y", "repo": "gone", "result": "no sha"}, tmp_path, seen, NOW) == NOW
    assert seen == {"Y": NOW.isoformat()}  # first seen done: kept in the watch's state


# --- the instrument's calibration against ccusage ------------------------------------------------


def _ccusage(tmp: Path, read: int) -> str:
    fake = tmp / "ccusage.py"
    fake.write_text(
        "import json, sys\n"
        f"print(json.dumps({{'daily': [{{'inputTokens': 0, 'cacheReadTokens': {read}, "
        "'cacheCreationTokens': 0, 'outputTokens': 0}]}))\n"
    )
    return f"{sys.executable} {fake}"


def test_ccusage_agreeing_with_the_reader_calibrates_it(tmp_path: Path) -> None:
    p = tmp_path / "projects"
    _agent(p, "one", "general-purpose", _resp("m1", _at(3), _usage(9000)))
    assert sw.calibrate(p, NOW, _ccusage(tmp_path, 9000))["status"] == "agrees"
    off = sw.calibrate(p, NOW, _ccusage(tmp_path, 8000))
    assert off["status"] == "disagrees" and "9000" in off["detail"] and "8000" in off["detail"]
    assert sw.calibrate(p, NOW, f"{tmp_path / 'missing'}")["status"] == "unverified"
    assert sw.calibrate(p, NOW, None)["status"] == "unverified"


def test_reported_faults_no_item_carries_become_candidates() -> None:
    rows = [
        {"agent_id": "a1234567890x", "time": "t", "faults": ["old fault", "new fault"]},
        {"agent_id": "b", "time": "t", "faults": ["old fault"]},
    ]
    got = sw.fault_candidates(rows, [{"id": "X", "faults": ["old fault"]}])
    assert [(c["id"], c["faults"], c["phase"]) for c in got] == [
        ("FAULTS.a123456789", ["new fault"], "FX")
    ]
    assert len(got[0]["done_state"]) >= 40


def test_a_week_with_no_agents_has_no_values_not_zeros(tmp_path: Path) -> None:
    """An empty week is not a 0% week: a delta against it would read as a jump from nothing."""
    m = sw.week([], NOW - dt.timedelta(days=7), NOW)
    assert m["agents"] == 0 and all(m[k] is None for k in sw.METRICS if k != "claude_md_bytes")
    assert m["cost_share"] is None


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
