#!/usr/bin/env python3
"""model_choice_watch.py -- is the model-choice rule still standing on what it was measured on?

The owner (2026-10-18): "continually improve this approach, otherwise we might get stuck with
outdated rules". Weekly (launchd), spending no tokens unless Claude Code itself changed:

1. FACTS: every fact in `hooks/claude/model_choice.json` is re-checked against its source -- a
   price row on the pricing page, a sentence on the model-config or cost pages, the Agent tool's
   `model`/`effort` enums read from the Claude Code binary, `claude --version`. A fact its source
   no longer bears out is `re-research`, with every tier resting on it. A source that cannot be
   read is `unverified` -- never a match.
2. LIVE PROOF: on a Claude Code version the rules were not proven on, the hook's live proof runs
   once per version: one haiku `claude -p` (budget-capped) whose spawns must be refused, then
   allowed with the contract, and ledgered (in a scratch ledger) with their outcome.
3. CANARY: red when sessions spawned agents in the last N days but no spawn reached the ledger
   (the hook fell off), and when ~/.claude/settings.json's goh hook entries differ from
   `hooks/claude/registrations.json` (nothing else enforces that).
4. AUDIT: `scripts/agent_audit.py` over the ledger -- silent failures and proposed rule changes.

Everything lands in ONE findings file (default `$XDG_CACHE_HOME/goh/model-choice-findings.md`,
the path the umbrella roadmap's owner-decision row names) and a one-line summary is printed.
Nothing here writes the rules file: changes are proposed, the owner makes them.

    python3 scripts/model_choice_watch.py             # run now
    python3 scripts/model_choice_watch.py --install   # write and load the weekly launchd job

Pinned by tests/test_model_choice_watch.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import mmap
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tui.lib import ok, warn

sys.path.insert(0, str(ROOT / "hooks" / "claude"))
sys.path.insert(0, str(ROOT / "scripts"))
import agent_audit
import model_choice
from agent_guard import STATUSES, ledger_path

LABEL = "com.ztomer.goh-model-choice-watch"
USER_AGENT = "goh-model-choice-watch/1"
WEEKDAY, HOUR = 1, 9  # Monday 09:30
SPAWN_TOOLS = ('"name":"Agent"', '"name":"Task"')
PROOF_DESC = "goh live proof allow"
PROOF_PROMPT = f"""This is an automated check of a Claude Code hook. Make exactly two Agent tool
calls, one after the other, and then reply with the word done.
1. description "goh live proof deny", subagent_type "general-purpose", prompt "Reply ok.", and NO
   model and NO effort. It is expected to be refused.
2. description "{PROOF_DESC}", subagent_type "general-purpose", model "haiku", effort "low",
   and this prompt, exactly:
There is no work to do: reply following the report contract below.
Model-choice: haiku@low because read-only
Pool: unfit because this checks the spawn hook itself"""
ENUM = {
    "model": rb"model:[\w$]+\((\[[^\]]*\])\)\.optional\(\)\.describe\(.Optional model override",
    "effort": rb"effort:[\w$]+\(([\w$]+|\[[^\]]*\])\)\.optional\(\)\.describe\(.Reasoning effort",
}


def _cache_dir() -> Path:
    return ledger_path().parent


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _fetch(url: str, pages: dict[str, str | Exception]) -> str:
    if url not in pages:
        try:
            # A named agent: the docs answer Python's default User-Agent with 403 (probed).
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as r:
                pages[url] = r.read().decode("utf-8", "replace")
        except (OSError, ValueError) as e:
            pages[url] = e
    page = pages[url]
    if isinstance(page, Exception):
        raise page
    return page


def _price_row(page: str, label: str) -> list[float] | None:
    for line in page.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and cells and cells[0] == label:
            return [float(x) for x in re.findall(r"\$([\d.]+)", " ".join(cells[1:]))]
    return None


def _enum(binary: bytes | mmap.mmap, field: str) -> list[str] | None:
    m = re.search(ENUM[field], binary)
    if not m:
        return None
    raw = m.group(1)
    if not raw.startswith(b"["):  # an identifier: find its definition
        d = re.search(rb"(?<![\w$])" + re.escape(raw) + rb"=(\[[^\]]*\])", binary)
        if not d:
            return None
        raw = d.group(1)
    try:
        return json.loads(raw)
    except ValueError:
        return None


def claude_version(claude: str) -> str | None:
    try:
        r = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    except OSError:
        return None
    m = re.match(r"\s*(\S+)", r.stdout)
    return m.group(1) if r.returncode == 0 and m else None


def check_fact(
    fact: dict[str, Any], claude: str | None, pages: dict[str, Any], binary: Any
) -> tuple[str, str]:
    """(ok | re-research | unverified, what the source says)."""
    chk, value = fact["check"], fact["value"]
    kind = chk["kind"]
    if kind == "claude_version":
        now = claude_version(claude) if claude else None
        if now is None:
            return "unverified", "claude --version did not answer"
        return ("ok", now) if now == value else ("re-research", f"claude --version says {now}")
    if kind == "agent_enum":
        if binary is None:
            return "unverified", "no Claude Code binary to read"
        found = _enum(binary, chk["field"])
        if found is None:
            return "unverified", f"the probe found no `{chk['field']}:` enum (schema moved?)"
        return ("ok", str(found)) if found == value else ("re-research", f"the binary says {found}")
    try:
        page = _fetch(chk["url"], pages)
    except (OSError, ValueError) as e:
        return "unverified", f"{chk['url']}: {e}"
    if kind == "price_row":
        row = _price_row(page, chk["label"])
        want = [float(value[k]) for k in model_choice.PRICE_KEYS]
        if row is None:
            return "re-research", f"no row {chk['label']!r} on the page"
        return ("ok", str(row)) if row == want else ("re-research", f"the page says {row}")
    missing = [t for t in chk["text"] if _norm(t) not in _norm(page)]
    if missing:
        return "re-research", f"no longer on the page: {missing[0]!r}"
    return "ok", "every sentence still there"


def check_facts(rules: model_choice.Rules, claude: str | None) -> list[dict[str, Any]]:
    pages: dict[str, Any] = {}
    binary = None
    if claude:
        try:
            with Path(claude).resolve().open("rb") as f:
                binary = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except (OSError, ValueError):
            binary = None
    out = []
    for name, fact in rules.facts.items():
        status, detail = check_fact(fact, claude, pages, binary)
        tiers = [k for k, t in rules.tiers.items() if name in t.get("rests_on", [])]
        out.append({"fact": name, "status": status, "detail": detail, "tiers": tiers,
                    "measured": fact["measured"], "source": fact["source"]})  # fmt: skip
    return out


def live_proof(claude: str) -> tuple[bool, str]:
    """The hook, live, on this Claude Code: refuses, allows with the contract, ledgers both ends."""
    with tempfile.TemporaryDirectory(prefix="goh-live-proof.") as tmp:
        env = dict(os.environ, XDG_CACHE_HOME=tmp)
        cmd = [claude, "-p", PROOF_PROMPT, "--model", "haiku", "--effort", "low",
               "--output-format", "stream-json", "--verbose", "--allowedTools", "Agent",
               "--max-budget-usd", "0.25"]  # fmt: skip
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env,
                               stdin=subprocess.DEVNULL, check=False)  # fmt: skip
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"claude -p did not finish ({e})"
        led = Path(tmp) / "goh" / "agent-spawns.jsonl"
        rows = agent_audit.read(led)[0] if led.exists() else []
    denied = "Refused: this general-purpose spawn" in r.stdout
    spawn = next((x for x in rows if x.get("event") == "spawn"
                  and x.get("description") == PROOF_DESC), None)  # fmt: skip
    outcome = spawn and next((x for x in rows if x.get("event") == "outcome"
                              and x.get("tool_use_id") == spawn.get("tool_use_id")), None)  # fmt: skip
    parts = {"refused": denied, "allowed + ledgered": spawn is not None,
             "outcome ledgered": bool(outcome),
             "contract reached the agent": bool(outcome) and outcome.get("status") in STATUSES}  # fmt: skip
    failed = [k for k, v in parts.items() if not v]
    return not failed, ("all of: " + ", ".join(parts)) if not failed else "failed: " + ", ".join(
        failed
    )


def canary(ledger: Path, projects: Path, days: int) -> dict[str, Any]:
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    spawns = 0
    if ledger.exists():
        for rec in agent_audit.read(ledger)[0]:
            if rec.get("event") == "spawn" and _when(rec.get("time")) >= since:
                spawns += 1
    uses = 0
    for path in projects.glob("**/*.jsonl"):
        try:
            if dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC) < since:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw in lines:
            if any(t in raw.replace(" ", "") for t in SPAWN_TOOLS) and _spawned(raw, since):
                uses += 1
    red = uses > 0 and spawns == 0
    detail = f"{uses} Agent call(s) in transcripts and {spawns} ledgered spawn(s) in {days} days"
    return {"red": red, "detail": detail, "uses": uses, "spawns": spawns}


def _when(stamp: Any) -> dt.datetime:
    try:
        when = dt.datetime.fromisoformat(str(stamp))
    except ValueError:
        return dt.datetime.min.replace(tzinfo=dt.UTC)
    return when if when.tzinfo else when.replace(tzinfo=dt.UTC)


def _spawned(raw: str, since: dt.datetime) -> bool:
    try:
        entry = json.loads(raw)
    except ValueError:
        return False
    content = (entry.get("message") or {}).get("content") if isinstance(entry, dict) else None
    return (isinstance(content, list) and _when(entry.get("timestamp")) >= since
            and any(isinstance(c, dict) and c.get("type") == "tool_use"
                    and c.get("name") in {"Agent", "Task"} for c in content))  # fmt: skip


def settings_drift(settings: Path, registrations: Path) -> dict[str, Any]:
    def key(event: str, matcher: Any, hook: dict[str, Any]) -> str:
        return f"{event} [{matcher or '*'}] {hook.get('command')} (timeout {hook.get('timeout')})"

    regs = json.loads(registrations.read_text(encoding="utf-8"))["hooks"]
    want = {key(ev, e.get("matcher"), e) for ev, es in regs.items() for e in es}
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"red": True, "missing": sorted(want), "extra": [], "detail": f"{settings}: {e}"}
    have = set()
    for ev, groups in (data.get("hooks") or {}).items():
        for g in groups or []:
            for h in g.get("hooks") or []:
                cmd = str(h.get("command", ""))
                if "hooks/claude/" in cmd and ("GOH_DIR" in cmd or "gates_of_heck" in cmd):
                    have.add(key(ev, g.get("matcher"), h))
    missing, extra = sorted(want - have), sorted(have - want)
    return {"red": bool(missing or extra), "missing": missing, "extra": extra,
            "detail": f"{len(missing)} missing, {len(extra)} not in registrations.json"}  # fmt: skip


def _findings(state: dict[str, Any], summary: str) -> str:
    lines = [f"# Model-choice findings — {state['ran_at']}", "",
             f"{summary}", "",
             "Written by goh `scripts/model_choice_watch.py` (weekly). Every row below is a",
             (f"PROPOSAL for the owner; nothing here changed the rules file "
              f"(`{state['rules']}`)."), ""]  # fmt: skip
    reds = [f"canary: {state['canary']['detail']}"] if state["canary"]["red"] else []
    if state["settings"]["red"]:
        reds += [
            f"settings.json vs registrations.json: missing {m}"
            for m in state["settings"]["missing"]
        ]
        reds += [
            f"settings.json vs registrations.json: not registered {m}"
            for m in state["settings"]["extra"]
        ]
    proof = state.get("live_proof") or {}
    if proof and not proof.get("ok"):
        reds.append(f"live proof on Claude Code {proof.get('version')}: {proof.get('detail')}")
    lines += ["## Red", ""] + ([f"- {r}" for r in reds] or ["- none"]) + [""]
    lines += ["## Facts to re-research (the source no longer says what the rules file says)", "",
              "| fact | rules file (measured) | source now | tiers resting on it |",
              "|---|---|---|---|"]  # fmt: skip
    marked = [f for f in state["facts"] if f["status"] == "re-research"]
    lines += [f"| {f['fact']} | {f['measured']} | {f['detail']} | {', '.join(f['tiers']) or '-'} |"
              for f in marked] or ["| none | | | |"]  # fmt: skip
    unv = [f for f in state["facts"] if f["status"] == "unverified"]
    lines += ["", "## Unverified (the source could not be read: not a match)", ""]
    lines += [f"- {f['fact']}: {f['detail']}" for f in unv] or ["- none"]
    if proof:
        lines += ["", (f"Live proof on {proof['version']} ({proof['date']}): "
                       f"{'passed' if proof['ok'] else 'FAILED'} -- {proof['detail']}")]  # fmt: skip
    audit = state.get("audit")
    lines += ["", "## Proposed rule changes (the audit's verdict arithmetic)", ""]
    if audit is None:
        lines.append("- no spawn ledger yet")
    else:
        lines += [f"- {p}" for p in audit["proposals"]] or [
            "- none (no class has a verdict that breaks)"
        ]
        v = audit["verification"]
        lines += ["", "## Landed reports against GitHub and CI", "",
                  (f"verified {v['verified']}, no shas {v['no_shas']}, "
                   f"unverified {len(v['unverified'])}, silent failures {len(v['silent_failure'])}"),
                  ""]  # fmt: skip
        lines += [f"- silent-failure: {s['time']} {s['class']} {s['first_line']}: {s['why']}"
                  for s in v["silent_failure"]]  # fmt: skip
    return "\n".join(lines) + "\n"


def plist(python: str, script: Path, log: Path) -> str:
    path = f"{Path.home() / '.local' / 'bin'}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{python}</string><string>{script}</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Weekday</key><integer>{WEEKDAY}</integer><key>Hour</key><integer>{HOUR}</integer><key>Minute</key><integer>30</integer></dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>{path}</string></dict>
</dict>
</plist>
"""


def install() -> int:
    target = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    log = _cache_dir() / "model-choice-watch.log"
    target.write_text(plist(sys.executable, Path(__file__).resolve(), log))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(target)], capture_output=True,
                   check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    r = subprocess.run(["launchctl", "bootstrap", domain, str(target)], capture_output=True,
                       text=True, check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    if r.returncode != 0:
        warn(f"launchctl bootstrap {target}: {r.stderr.strip()}")
        return 1
    ok(f"weekly (Monday {HOUR:02d}:30): {target} (log {log})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    cache = _cache_dir()
    ap.add_argument("--findings", type=Path, default=cache / "model-choice-findings.md")
    ap.add_argument("--state", type=Path, default=cache / "model-choice-watch.json")
    ap.add_argument("--ledger", type=Path, default=ledger_path())
    ap.add_argument("--settings", type=Path, default=Path.home() / ".claude" / "settings.json")
    ap.add_argument("--projects", type=Path, default=Path.home() / ".claude" / "projects")
    ap.add_argument("--claude", default=shutil.which("claude"), help="the Claude Code CLI")
    ap.add_argument("--root", type=Path,
                    default=Path(os.environ.get("GOH_ESTATE_ROOT", Path.home() / "Projects")))  # fmt: skip
    ap.add_argument("--days", type=int, default=7, help="the canary's window")
    ap.add_argument("--offline-audit", action="store_true", help="the audit asks GitHub nothing")
    ap.add_argument("--no-live-proof", action="store_true", help="never spend tokens")
    ap.add_argument("--install", action="store_true", help="write and load the weekly launchd job")
    args = ap.parse_args(argv)
    if args.install:
        return install()
    rules = model_choice.load()
    try:
        prior = json.loads(args.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prior = {}
    facts = check_facts(rules, args.claude)
    version = claude_version(args.claude) if args.claude else None
    changed = any(
        f["fact"] == "claude_code.version" and f["status"] == "re-research" for f in facts
    )
    proof = prior.get("live_proof")
    if changed and args.claude and version and (proof or {}).get("version") != version:
        if args.no_live_proof:
            proof = {"version": version, "ok": False, "date": "-",
                     "detail": "not run (--no-live-proof)"}  # fmt: skip
        else:
            passed, detail = live_proof(args.claude)
            proof = {"version": version, "ok": passed, "detail": detail,
                     "date": dt.date.today().isoformat()}  # fmt: skip
    state: dict[str, Any] = {
        "ran_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "rules": str(rules.path),
        "facts": facts, "live_proof": proof,
        "canary": canary(args.ledger, args.projects, args.days),
        "settings": settings_drift(args.settings, ROOT / "hooks" / "claude" / "registrations.json"),
        "audit": agent_audit.run(args.ledger, args.root, args.offline_audit),
    }  # fmt: skip
    red = (
        state["canary"]["red"]
        or state["settings"]["red"]
        or (proof is not None and not proof["ok"])
    )
    n_re = sum(f["status"] == "re-research" for f in facts)
    n_unv = sum(f["status"] == "unverified" for f in facts)
    audit = state["audit"] or {"proposals": [], "verification": {"silent_failure": []}}
    summary = (f"model-choice watch: {'RED' if red else 'green'}, {n_re} re-research, {n_unv} unverified, "
               f"{len(audit['proposals'])} proposal(s), "
               f"{len(audit['verification']['silent_failure'])} silent failure(s) -> {args.findings}")  # fmt: skip
    for path, text in ((args.state, json.dumps(state, indent=1, default=str) + "\n"),
                       (args.findings, _findings(state, summary))):  # fmt: skip
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(summary)
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
