"""The weekly watch keeps the model-choice rules from going stale without anyone remembering to look.

`scripts/model_choice_watch.py` spends no tokens unless Claude Code itself changed. Each run:

* re-checks every fact in `hooks/claude/model_choice.json` against its source -- the pricing and
  model-config pages, the Agent tool's `model`/`effort` enums in the Claude Code binary, and
  `claude --version` -- and marks a fact that no longer matches, and every tier resting on it,
  `re-research`. A source it cannot reach is `unverified`, never a match;
* on a Claude Code version the rules were not proven on, re-runs the hook's live proof once (one
  cheap haiku `claude -p`: a spawn refused, a spawn allowed with the contract, both ledgered);
* goes red when no spawn reached the ledger in N days while sessions spawned agents (the hook fell
  off), and when ~/.claude/settings.json's goh hook entries differ from registrations.json;
* runs the audit, and writes everything to one findings file the umbrella session reads, plus a
  one-line summary. It proposes; it never edits the rules file.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Self

import pytest
from conftest import REPO_ROOT, hermetic_env

WATCH = REPO_ROOT / "scripts" / "model_choice_watch.py"
RULES = json.loads((REPO_ROOT / "hooks" / "claude" / "model_choice.json").read_text())
REGS = json.loads((REPO_ROOT / "hooks" / "claude" / "registrations.json").read_text())
KEYS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
NOW = dt.datetime.now(dt.UTC)

FAKE_CLAUDE = """#!{python}
# A stand-in for the Claude Code binary. The probe reads these bytes for the Agent tool's schema:
# {schema}
import json, os, pathlib, sys
here = pathlib.Path(__file__).parent
if sys.argv[1:] == ["--version"]:
    print("{version} (Claude Code)")
    sys.exit(0)
(here / "proof-ran").write_text("x")
if (here / "proof-fails").exists():
    print("{{}}")
    sys.exit(0)
print(json.dumps({{"type": "user", "message": {{"content": [{{"type": "tool_result",
      "content": "Refused: this general-purpose spawn -- it names no model"}}]}}}}))
led = pathlib.Path(os.environ["XDG_CACHE_HOME"]) / "goh" / "agent-spawns.jsonl"
led.parent.mkdir(parents=True, exist_ok=True)
rows = [{{"event": "spawn", "session_id": "p", "tool_use_id": "t", "model": "haiku",
          "effort": "low", "description": "goh live proof allow"}},
        {{"event": "outcome", "session_id": "p", "tool_use_id": "t", "status": "answered"}}]
led.write_text("".join(json.dumps(r) + "\\n" for r in rows))
"""
SCHEMA = ('model:W(["sonnet","opus","haiku","fable"]).optional().describe(`Optional model override '
          'for this agent. effort:W(Mc).optional().describe("Reasoning effort for this agent. '
          'var Mc=["low","medium","high","xhigh","max"];')  # fmt: skip


def _page(rules: dict[str, Any], url: str) -> str:
    """The page at `url` saying exactly what the rules file says it says."""
    out = []
    for fact in rules["facts"].values():
        chk = fact["check"]
        if chk.get("url") != url:
            continue
        if chk["kind"] == "price_row":
            cells = " | ".join(f"${fact['value'][k]} / MTok" for k in KEYS)
            out.append(f"| {chk['label']}   | {cells} |")
        else:
            out += chk["text"]
    return "\n\n".join(out) + "\n"


class World:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.rules = json.loads(json.dumps(RULES))
        self.pages: dict[str, str] = {}
        for fact in self.rules["facts"].values():
            url = fact["check"].get("url")
            if url and url not in self.pages:
                self.pages[url] = _page(RULES, url)
        self.version = RULES["proven_on"]["claude_code"]
        self.schema = SCHEMA
        self.settings = {"hooks": {}}
        for event, entries in REGS["hooks"].items():
            for e in entries:
                group = {"hooks": [{"type": "command", "command": e["command"],
                                    "timeout": e["timeout"]}]}  # fmt: skip
                if e["matcher"] is not None:
                    group["matcher"] = e["matcher"]
                self.settings["hooks"].setdefault(event, []).append(group)
        self.ledger: list[dict[str, Any]] = []
        self.transcript: list[dict[str, Any]] = []

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        t = self.tmp
        rules = json.loads(json.dumps(self.rules))
        for i, (url, text) in enumerate(self.pages.items()):
            page = t / f"page{i}.md"
            page.write_text(text)
            for fact in rules["facts"].values():
                if fact["check"].get("url") == url:
                    fact["check"]["url"] = page.as_uri()
        (t / "rules.json").write_text(json.dumps(rules))
        claude = t / "bin" / "claude"
        claude.parent.mkdir(exist_ok=True)
        claude.write_text(FAKE_CLAUDE.format(python=sys.executable, schema=self.schema,
                                             version=self.version))  # fmt: skip
        claude.chmod(0o755)
        (t / "settings.json").write_text(json.dumps(self.settings))
        (t / "ledger.jsonl").write_text("".join(json.dumps(x) + "\n" for x in self.ledger))
        proj = t / "projects" / "-p"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "s.jsonl").write_text("".join(json.dumps(x) + "\n" for x in self.transcript))
        return subprocess.run(
            [sys.executable, str(WATCH), "--claude", str(claude), "--settings",
             str(t / "settings.json"), "--ledger", str(t / "ledger.jsonl"), "--projects",
             str(t / "projects"), "--findings", str(t / "findings.md"), "--state",
             str(t / "state.json"), "--root", str(t), "--offline-audit", *args],
            capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, check=False,
            env=hermetic_env(NO_COLOR="1", GOH_MODEL_CHOICE=str(t / "rules.json"),
                             XDG_CACHE_HOME=str(t / "cache")),
        )  # fmt: skip

    def state(self) -> dict[str, Any]:
        return json.loads((self.tmp / "state.json").read_text())

    def findings(self) -> str:
        return (self.tmp / "findings.md").read_text()


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _facts(w: World) -> dict[str, dict[str, Any]]:
    return {f["fact"]: f for f in w.state()["facts"]}


def test_sources_that_still_say_what_the_rules_say_are_all_ok(world: World) -> None:
    r = world.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert {f["status"] for f in _facts(world).values()} == {"ok"}, world.state()["facts"]
    summary = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(summary) == 1 and "0 re-research" in summary[0] and "findings.md" in summary[0]
    assert "# Model-choice findings" in world.findings()
    assert not (world.tmp / "bin" / "proof-ran").exists()  # no version change: no tokens


def test_a_changed_price_marks_the_fact_and_its_tiers_re_research(world: World) -> None:
    url = RULES["facts"]["price.sonnet"]["check"]["url"]
    world.pages[url] = world.pages[url].replace("| $2 / MTok", "| $3 / MTok", 1)
    world.run()
    fact = _facts(world)["price.sonnet"]
    assert fact["status"] == "re-research" and "3" in fact["detail"]
    assert {"sonnet@high", "sonnet@xhigh"} <= set(fact["tiers"])
    assert _facts(world)["price.opus"]["status"] == "ok"
    text = world.findings()
    assert "price.sonnet" in text and "re-research" in text
    assert json.loads((world.tmp / "rules.json").read_text())["facts"]["price.sonnet"]["value"][
        "input"] == 2  # fmt: skip


def test_text_gone_from_its_page_is_re_research(world: World) -> None:
    url = RULES["facts"]["claude_code.default_effort"]["check"]["url"]
    world.pages[url] = world.pages[url].replace("default to `medium`", "default to `high`")
    world.run()
    assert _facts(world)["claude_code.default_effort"]["status"] == "re-research"


def test_an_unreachable_source_is_unverified_never_ok(world: World) -> None:
    world.rules["facts"]["price.opus"]["check"]["url"] = (world.tmp / "gone.md").as_uri()
    world.run()
    assert _facts(world)["price.opus"]["status"] == "unverified"


def test_a_changed_agent_model_enum_is_re_research(world: World) -> None:
    world.schema = SCHEMA.replace('"fable"]', '"fable","mythos"]')
    world.run()
    assert _facts(world)["agent.models"]["status"] == "re-research"
    assert _facts(world)["agent.efforts"]["status"] == "ok"


def test_a_schema_the_probe_cannot_find_is_unverified(world: World) -> None:
    world.schema = "nothing here"
    world.run()
    assert _facts(world)["agent.models"]["status"] == "unverified"


def test_a_new_claude_code_version_reruns_the_live_proof_once(world: World) -> None:
    world.version = "2.1.999"
    r = world.run()
    assert _facts(world)["claude_code.version"]["status"] == "re-research"
    assert (world.tmp / "bin" / "proof-ran").exists()
    proof = world.state()["live_proof"]
    assert proof["version"] == "2.1.999" and proof["ok"] is True, proof
    assert r.returncode == 0, r.stdout
    (world.tmp / "bin" / "proof-ran").unlink()
    world.run()
    assert not (world.tmp / "bin" / "proof-ran").exists()  # once per version


def test_a_failed_live_proof_is_red(world: World) -> None:
    world.version = "2.1.999"
    (world.tmp / "bin").mkdir()
    (world.tmp / "bin" / "proof-fails").write_text("x")
    r = world.run()
    assert world.state()["live_proof"]["ok"] is False
    assert r.returncode == 1 and "live proof" in world.findings()


def _agent_use(days_ago: float) -> dict[str, Any]:
    when = (NOW - dt.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {"type": "assistant", "timestamp": when,
            "message": {"content": [{"type": "tool_use", "name": "Agent", "input": {}}]}}  # fmt: skip


def test_the_canary_is_red_when_spawns_ran_but_none_reached_the_ledger(world: World) -> None:
    world.transcript = [_agent_use(1)]
    r = world.run("--days", "7")
    assert world.state()["canary"]["red"] is True and r.returncode == 1
    assert "canary" in world.findings()


def test_the_canary_is_green_when_the_ledger_saw_a_spawn(world: World) -> None:
    world.transcript = [_agent_use(1)]
    world.ledger = [{"event": "spawn", "time": (NOW - dt.timedelta(days=1)).isoformat()}]
    world.run("--days", "7")
    assert world.state()["canary"]["red"] is False


def test_the_canary_is_green_when_nothing_spawned(world: World) -> None:
    world.transcript = [_agent_use(30)]  # outside the window
    world.run("--days", "7")
    assert world.state()["canary"]["red"] is False


def test_a_goh_hook_missing_from_settings_is_red(world: World) -> None:
    world.settings["hooks"].pop("SubagentStop")
    r = world.run()
    drift = world.state()["settings"]
    assert drift["red"] is True and any("SubagentStop" in m for m in drift["missing"])
    assert r.returncode == 1


def test_a_goh_hook_settings_has_but_registrations_lacks_is_red(world: World) -> None:
    extra = {"hooks": [{"type": "command", "timeout": 5,
                        "command": 'python3 "${GOH_DIR:-$HOME/Projects/gates_of_heck}/hooks/claude/old.py"'}]}  # fmt: skip
    world.settings["hooks"].setdefault("Stop", []).append(extra)
    world.settings["hooks"]["PreToolUse"][0]["hooks"].insert(0, {"type": "command",
                                                                  "command": "rtk hook claude"})  # fmt: skip
    world.run()
    drift = world.state()["settings"]
    assert drift["red"] is True and drift["missing"] == []
    assert len(drift["extra"]) == 1 and "old.py" in drift["extra"][0]  # rtk's is not goh's


def test_the_schedule_is_weekly_and_runs_this_script() -> None:
    sys.path.insert(0, str(WATCH.parent))
    import model_choice_watch

    plist = model_choice_watch.plist("/usr/bin/python3", WATCH, Path("/tmp/x.log"))
    assert "<key>Weekday</key>" in plist and str(WATCH) in plist
    assert re.search(r"<key>RunAtLoad</key>\s*<false/>", plist)


def test_the_fetch_names_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """The docs answer Python's default User-Agent with HTTP 403 (live, 2026-10-18): every fact
    read `unverified` until the request named an agent."""
    sys.path.insert(0, str(WATCH.parent))
    import model_choice_watch

    seen = []

    class Page:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return b"page"

    def urlopen(req: Any, timeout: float) -> Page:
        seen.append(req.get_header("User-agent"))
        return Page()

    monkeypatch.setattr(model_choice_watch.urllib.request, "urlopen", urlopen)
    assert model_choice_watch._fetch("https://example.invalid/x.md", {}) == "page"
    assert seen == [model_choice_watch.USER_AGENT]
