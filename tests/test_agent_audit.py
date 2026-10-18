"""The spawn ledger shows a wrong model choice in BOTH directions, and says when it cannot yet.

`hooks/claude/agent_guard.py` writes one line per subagent spawn (its declared model@effort and
tags) and one per outcome (tokens, `Status:`). `scripts/agent_audit.py` reads them:

* UNDER -- a cheap-tier spawn (sonnet@high, opus@low) that ended blocked or failed, or that was
  followed in its session by an `escalation` spawn of the same item: the cheap tier was too cheap.
* OVER -- an opus@medium-or-above spawn that landed under the token threshold, the median of the
  cheap tier's landed spawns: work that cost what cheap-tier work costs was cheap-tier work.

A class with fewer than 20 outcomes gets no verdict, and neither does a threshold drawn from
fewer than 20: "insufficient data (n=...)". A verdict over five spawns is a guess with a number on
it (model-choice-decision.md, "Still unmeasured").
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from conftest import REPO_ROOT, hermetic_env

AUDIT = REPO_ROOT / "scripts" / "agent_audit.py"


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path, self.lines, self.n = path, [], 0

    def spawn(self, choice: str, *, status: str | None = "landed", tokens: int = 100_000,
              session: str = "s", desc: str | None = None, tags: list[str] | None = None) -> None:  # fmt: skip
        model, effort = choice.split("@")
        self.n += 1
        tid = f"toolu_{self.n}"
        self.lines.append({
            "event": "spawn", "time": f"2026-10-18T10:{self.n // 60:02d}:{self.n % 60:02d}+00:00",
            "session_id": session, "tool_use_id": tid, "model": model, "effort": effort,
            "tags": tags or [], "exempt": False, "description": desc or f"item {self.n}",
            "first_line": f"item {self.n}",
        })  # fmt: skip
        if status is not None:
            self.lines.append({"event": "outcome", "session_id": session, "tool_use_id": tid,
                               "status": status, "tokens": {"total": tokens}})  # fmt: skip

    def write(self) -> Path:
        self.path.write_text("".join(json.dumps(x) + "\n" for x in self.lines))
        return self.path


def _audit(ledger: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AUDIT), "--ledger", str(ledger), *args],
        capture_output=True, text=True, env=hermetic_env(NO_COLOR="1"), timeout=30,
        stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip


def _json(ledger: Path) -> dict[str, Any]:
    r = _audit(ledger, "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_a_class_under_twenty_outcomes_gets_no_verdict(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "l.jsonl")
    for _ in range(19):
        led.spawn("sonnet@high", status="failed")
    path = led.write()
    under = _json(path)["under"]["sonnet@high"]
    assert under["n"] == 19 and under["verdict"] is None and under["flagged"] == []
    assert "insufficient data (n=19)" in _audit(path).stdout


def test_under_flags_a_cheap_spawn_that_failed_or_was_escalated(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "l.jsonl")
    for _ in range(16):
        led.spawn("sonnet@high")
    led.spawn("sonnet@high", status="failed")
    led.spawn("sonnet@high", status="blocked")
    led.spawn("sonnet@high", desc="port the parser")  # landed, then escalated: still too cheap
    led.spawn("opus@xhigh", desc="port the parser", tags=["escalation"])
    led.spawn("sonnet@high", desc="fix the leak", session="s")
    led.spawn("opus@xhigh", desc="fix the leak", tags=["escalation"], session="other")
    under = _json(led.write())["under"]["sonnet@high"]
    assert under["n"] == 20
    assert sorted(f["why"] for f in under["flagged"]) == ["blocked", "escalated", "failed"]
    assert under["verdict"] == "3 of 20 too cheap (15%)"


def test_under_is_per_cheap_class(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "l.jsonl")
    for _ in range(20):
        led.spawn("sonnet@high")
    led.spawn("opus@low", status="failed")
    report = _json(led.write())["under"]
    assert report["sonnet@high"]["verdict"] == "0 of 20 too cheap (0%)"
    assert report["opus@low"]["n"] == 1 and report["opus@low"]["verdict"] is None


def _with_reference(led: Ledger, tokens: int = 100_000) -> None:
    for _ in range(20):
        led.spawn("sonnet@high", tokens=tokens)


def test_over_flags_an_opus_spawn_that_landed_under_the_cheap_tier_median(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "l.jsonl")
    _with_reference(led)
    for i in range(20):
        led.spawn("opus@high", tokens=50_000 if i < 5 else 300_000, tags=["research"])
    led.spawn("opus@medium", tokens=10_000, tags=["design-ui"])  # 1 outcome: no verdict
    report = _json(led.write())
    assert report["threshold"] == {"tokens": 100_000, "n": 20}
    over = report["over"]["opus@high"]
    assert over["n"] == 20 and len(over["flagged"]) == 5
    assert over["verdict"] == "5 of 20 landed under 100000 tokens (25%)"
    assert report["over"]["opus@medium"]["verdict"] is None


def test_over_gets_no_verdict_while_its_threshold_rests_on_too_few(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "l.jsonl")
    for _ in range(19):
        led.spawn("sonnet@high")
    for _ in range(20):
        led.spawn("opus@high", tokens=1_000, tags=["research"])
    path = led.write()
    report = _json(path)
    assert report["threshold"] == {"tokens": None, "n": 19}
    assert report["over"]["opus@high"]["verdict"] is None
    assert "insufficient data (n=19)" in _audit(path).stdout


def test_an_opus_spawn_that_did_not_land_is_never_over(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "l.jsonl")
    _with_reference(led)
    for _ in range(20):
        led.spawn("opus@high", status="failed", tokens=1_000, tags=["research"])
    assert _json(led.write())["over"]["opus@high"]["flagged"] == []


def test_no_ledger_is_said_plainly(tmp_path: Path) -> None:
    r = _audit(tmp_path / "absent.jsonl")
    assert r.returncode == 0, r.stderr
    assert "no spawn ledger" in r.stdout + r.stderr


def test_a_torn_line_is_skipped_and_counted(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "l.jsonl")
    led.spawn("sonnet@high")
    path = led.write()
    with path.open("a") as f:
        f.write('{"event": "spawn", "trunc\n')
    assert _json(path)["unreadable_lines"] == 1
