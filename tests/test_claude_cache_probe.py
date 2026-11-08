"""scripts/claude_cache_probe.py: the measurements the managed read cap and cache TTL were chosen by.

Synthetic subagent transcripts in the shape Claude Code 2.1.311 writes (`assistant` records with
`message.usage` and `cache_creation.ephemeral_{5m,1h}_input_tokens`, `user` records with
`tool_result` blocks), so each number the probe reports is checked against one worked out by hand.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import claude_cache_probe as probe

CUT = "2026-11-08T12:00:00Z"


def _turn(n: int, at: str, *, read: int, w5: int = 0, w1: int = 0, out: int = 0,
          content: list | None = None) -> dict:  # fmt: skip
    return {"type": "assistant", "timestamp": at,
            "message": {"id": f"m{n}", "content": content or [],
                        "usage": {"input_tokens": 0, "output_tokens": out,
                                  "cache_read_input_tokens": read,
                                  "cache_creation_input_tokens": w5 + w1,
                                  "cache_creation": {"ephemeral_5m_input_tokens": w5,
                                                     "ephemeral_1h_input_tokens": w1}}}}  # fmt: skip


def _result(at: str, use: str, content: object) -> dict:
    return {"type": "user", "timestamp": at,
            "message": {"content": [{"type": "tool_result", "tool_use_id": use, "content": content}]}}  # fmt: skip


def _write(tmp: Path, name: str, recs: list[dict]) -> Path:
    p = tmp / "proj" / "sess" / "subagents" / f"agent-{name}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return tmp / "proj"


def test_ttl_prices_the_other_ttl_on_the_same_turns(tmp_path: Path) -> None:
    """After the cut the agent ran 1h; one 10-minute gap. Hand-worked: one big miss avoided, so 1h is cheaper here."""
    recs = [
        _turn(1, "2026-11-08T13:00:00Z", read=1000, w1=9000),  # first turn: shared prefix 1000
        _turn(2, "2026-11-08T13:01:00Z", read=10000, w1=500),
        _turn(3, "2026-11-08T13:11:00Z", read=10500, w1=200),  # 10 min gap: a 1h hit
    ]
    out = probe.ttl(probe.transcripts(_write(tmp_path, "a", recs)), probe._ts(CUT),
                    probe._ts("2026-11-07T12:00:00Z"))  # fmt: skip
    after = out["after"]
    assert after["ran"] == "1h" and after["gap_turns"] == 1 and after["write_1h"] == 9700
    ran = 21500 * 0.1 + 9700 * 2.0
    # under 5m the gap turn re-writes everything but the 1000-token shared prefix
    other = (1000 + 10000 + 1000) * 0.1 + (9000 + 500 + 9700) * 1.25
    assert after["cost"] == {"1h": round(ran), "5m": round(other)}
    assert after["ran_vs_other"] == round(ran / other - 1, 4) < 0
    assert out["before"]["turns"] == 0


def test_a_turn_split_over_records_is_counted_once_and_a_gap_over_an_hour_misses_both(
    tmp_path: Path,
) -> None:
    t1 = _turn(1, "2026-11-08T13:00:00Z", read=1000, w5=9000)
    recs = [t1, t1, _turn(2, "2026-11-08T15:00:00Z", read=1000, w5=9500)]
    out = probe.ttl(probe.transcripts(_write(tmp_path, "b", recs)), probe._ts(CUT),
                    probe._ts("2026-11-07T12:00:00Z"))  # fmt: skip
    after = out["after"]
    assert after["turns"] == 2 and after["gap_turns"] == 1 and after["gap_write_share"] == 0.514
    assert after["cost"]["5m"] == after["cost"]["1h"] - round(
        18500 * 0.75
    )  # only the write rate differs


def test_reads_sizes_text_reads_only_and_measures_its_own_token_ratio(tmp_path: Path) -> None:
    def use(n: int, uid: str, args: dict, at: str, read: int, out: int = 0) -> dict:
        return _turn(n, at, read=read, out=out,
                     content=[{"type": "tool_use", "id": uid, "name": "Read", "input": args}])  # fmt: skip

    big = "x" * 30000
    recs = [
        use(1, "u1", {"file_path": "/a"}, "2026-11-08T13:00:00Z", read=5000, out=100),
        _result("2026-11-08T13:00:01Z", "u1", big),
        use(
            2,
            "u2",
            {"file_path": "/b", "offset": 1, "limit": 10},
            "2026-11-08T13:00:02Z",
            read=5000 + 100 + 10000,
        ),  # grew by 10000 tokens for 30000 chars: 3.0
        _result("2026-11-08T13:00:03Z", "u2", "y" * 300),
        use(3, "u3", {"file_path": "/c.png"}, "2026-11-08T13:00:04Z", read=15200),
        _result("2026-11-08T13:00:05Z", "u3", [{"type": "image", "source": {}}]),
        _turn(4, "2026-11-08T13:00:06Z", read=15300),
    ]
    out = probe.reads(probe.transcripts(_write(tmp_path, "c", recs)), probe._ts(CUT),
                      probe._ts("2026-11-07T12:00:00Z"))  # fmt: skip
    assert out["chars_per_token"] == 3.0 and out["ratio_samples"] == 1
    after = out["after"]
    assert (after["reads"], after["whole"], after["ranged"], after["images"]) == (2, 1, 1, 1)
    assert after["whole_tokens"] == [10000]
    assert after["caps"][8000] == {"over": 1, "tokens_over": round(2000 / 10100, 3)}
    assert after["caps"][12000]["over"] == 0


def test_the_cli_reports_both_windows_and_refuses_a_bad_cut(tmp_path: Path) -> None:
    root = _write(tmp_path, "d", [_turn(1, "2026-11-08T13:00:00Z", read=1, w5=1)])
    run = [sys.executable, str(REPO_ROOT / "scripts" / "claude_cache_probe.py")]
    r = subprocess.run([*run, "ttl", "--cut", CUT, "--projects", str(root), "--json"],
                       capture_output=True, text=True, env=hermetic_env(), timeout=60,
                       check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert set(json.loads(r.stdout)) == {"before", "after"}
    bad = subprocess.run([*run, "ttl", "--cut", "yesterday", "--projects", str(root)],
                         capture_output=True, text=True, env=hermetic_env(), timeout=60,
                         check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    assert bad.returncode == 2
