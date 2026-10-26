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
              session: str = "s", desc: str | None = None, tags: list[str] | None = None,
              usage: dict[str, int] | None = None) -> None:  # fmt: skip
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
                               "status": status,
                               "tokens": usage or {"total": tokens}})  # fmt: skip

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


# --- the verdict: does the cheap tier still pay for itself? ----------------------------------------
#
# Cheap first and escalate on a miss costs c + r*o per item; Opus from the start costs o. So the
# cheap tier holds while its redo rate r < 1 - c/o, with c and o the mean cost per attempt from the
# ledger's own tokens at the rules file's prices (o: the escalation class, opus@high). Output is a
# list of PROPOSED rule changes with their numbers; the audit never writes the rules file.

CHEAP_USE = {"input": 0, "output": 10_000, "cache_creation": 0, "cache_read": 1_000_000}  # $0.20
OPUS_USE = {"input": 0, "output": 20_000, "cache_creation": 0, "cache_read": 2_000_000}  # $0.80


def _priced(led: Ledger, redo: int, opus_n: int = 20) -> None:
    for i in range(20):
        led.spawn("sonnet@high", status="failed" if i < redo else "landed", usage=CHEAP_USE)
    for _ in range(opus_n):
        led.spawn("opus@high", usage=OPUS_USE, tags=["research"])


def test_a_cheap_class_under_its_break_even_redo_rate_holds(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "l.jsonl")
    _priced(led, redo=3)
    report = _json(led.write())
    v = report["verdicts"]["sonnet@high"]
    assert v["n"] == 20 and v["redo"] == 3
    assert round(v["cost"], 4) == 0.2 and round(v["opus_cost"], 4) == 0.8
    assert round(v["break_even"], 4) == 0.75 and v["holds"] is True
    assert report["proposals"] == []


def test_a_cheap_class_over_its_break_even_redo_rate_is_proposed_not_applied(
    tmp_path: Path,
) -> None:
    led = Ledger(tmp_path / "l.jsonl")
    _priced(led, redo=16)
    rules = tmp_path / "rules.json"
    rules.write_bytes((REPO_ROOT / "hooks" / "claude" / "model_choice.json").read_bytes())
    before = rules.read_bytes()
    r = subprocess.run(
        [sys.executable, str(AUDIT), "--ledger", str(led.write()), "--json"],
        capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL, check=False,
        env=hermetic_env(NO_COLOR="1", GOH_MODEL_CHOICE=str(rules)),
    )  # fmt: skip
    report = json.loads(r.stdout)
    assert report["verdicts"]["sonnet@high"]["holds"] is False
    (proposal,) = report["proposals"]
    for needle in ("sonnet@high", "80%", "75%", "$0.200", "$0.800", "n=20"):
        assert needle in proposal, (needle, proposal)
    assert rules.read_bytes() == before  # proposed, never applied


def test_no_verdict_until_the_escalation_class_has_enough_outcomes_too(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "l.jsonl")
    _priced(led, redo=16, opus_n=19)
    report = _json(led.write())
    assert report["verdicts"]["sonnet@high"]["holds"] is None and report["proposals"] == []


def test_a_silent_failure_is_a_redo(tmp_path: Path) -> None:
    """A `landed` whose sha GitHub does not have is a cheap choice that failed, quietly."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    import agent_audit
    import landed_verify

    led = Ledger(tmp_path / "l.jsonl")
    _priced(led, redo=0)
    records = agent_audit.read(led.write())[0]
    first = next(r for r in records if r["event"] == "spawn")
    key = (first["session_id"], first["tool_use_id"])
    silent = {key: landed_verify.Verdict("silent-failure", ["abc1234"], "not on o/r main")}
    report = agent_audit.audit(records, agent_audit.model_choice.load(), 20, silent)
    under = report["under"]["sonnet@high"]
    assert under["redo"] == 1 and under["flagged"][0]["why"] == "silent-failure"
    assert len(report["verification"]["silent_failure"]) == 1


def test_faults_lists_each_outcome_that_saw_one(tmp_path: Path) -> None:
    led = tmp_path / "l.jsonl"
    rows = [
        {"event": "outcome", "time": "2026-10-20T00:00:00+00:00", "agent_id": "old", "report": "x"},
        {"event": "outcome", "time": "2026-10-21T00:00:00+00:00", "agent_id": "n", "faults": []},
        {
            "event": "outcome",
            "time": "2026-10-22T00:00:00+00:00",
            "agent_id": "a1",
            "session_id": "s1",
            "report": "Status: landed\nfe071cc one\nFaults seen: W1; W2",
            "faults": ["W1", "W2"],
        },
        {
            "event": "outcome",
            "time": "2026-10-19T00:00:00+00:00",
            "agent_id": "early",
            "faults": ["E"],
        },
    ]
    led.write_text("".join(json.dumps(x) + "\n" for x in rows))
    r = _audit(led, "--faults", "--since", "2026-10-20", "--json")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [{"agent_id": "a1", "session_id": "s1",
                                     "time": "2026-10-22T00:00:00+00:00", "first_sha": "fe071cc",
                                     "faults": ["W1", "W2"]}]  # fmt: skip
    assert "W2" in _audit(led, "--faults").stdout
