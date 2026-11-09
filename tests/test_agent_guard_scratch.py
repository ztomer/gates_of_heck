"""GOH.AGENTSCRATCH: a subagent's fixed-name file in the shared scratchpad ROOT is flagged.

Every subagent of a session shares the coordinator's scratchpad
(`/private/tmp/claude-<uid>/<proj>/<session>/scratchpad`), so two agents writing `release.log`
there overwrite each other (LOOP.SCRATCH). The agent definitions say to claim a per-item path with
`loopctl scratch <ITEM-ID> --base <scratchpad>`; at SubagentStop, agent_guard reads the agent's
transcript and names every write it made straight into the root. Transcripts here are FAKE.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env

GUARD = REPO_ROOT / "hooks" / "claude" / "agent_guard.py"
AGENTS = REPO_ROOT / "hooks" / "claude" / "agents"
sys.path.insert(0, str(GUARD.parent))
import scratch_guard as sg

PAD = "/private/tmp/claude-501/-Users-x-Projects/abd8a1d9-76a2-4e7f-8b43-c4ef25f4a067/scratchpad"


def _use(name: str, **inp: str) -> dict:
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t", "name": name, "input": inp}]}}  # fmt: skip


def _text(recs: list[dict]) -> str:
    return "".join(json.dumps(r) + "\n" for r in recs)


@pytest.mark.parametrize(
    "rec",
    [
        _use("Write", file_path=f"{PAD}/release.log", content="x"),
        _use("Bash", command=f"tools/release.sh 1.2.3 > {PAD}/release.log 2>&1"),
        _use("Bash", command=f"make 2>&1 | tee -a {PAD}/build.log"),
        _use("Bash", command=f"curl -s -o {PAD}/page.html https://x"),
        _use("Bash", command=f"cat > '{PAD}/notes.txt' <<'X'\nhi\nX"),
        _use("Edit", file_path=f"{PAD}/plan.md", old_string="a", new_string="b"),
    ],
    ids=["write", "redirect", "tee", "curl-o", "quoted-heredoc", "edit"],
)
def test_a_fixed_name_write_into_the_scratchpad_root_is_flagged(rec: dict) -> None:
    (hit,) = sg.loose_writes(_text([rec]))
    assert hit.startswith(PAD + "/") and "/" not in hit.removeprefix(PAD + "/")


@pytest.mark.parametrize(
    "rec",
    [
        _use("Write", file_path=f"{PAD}/GOH.X/release.log", content="x"),  # the item's own dir
        _use("Bash", command=f"tools/release.sh > {PAD}/GOH.X/release.log"),
        _use("Bash", command=f"cat {PAD}/release.log; ls {PAD}"),  # a read is not a write
        _use("Bash", command=f'd=$(python3 loopctl.py scratch GOH.X --base {PAD}); echo > "$d/a"'),
        _use("Write", file_path="/Users/x/Projects/repo/release.log", content="x"),
        _use("Bash", command=f"mkdir -p {PAD}/GOH.X"),
    ],
    ids=["item-dir", "item-dir-redirect", "read", "claimed", "repo-file", "mkdir"],
)
def test_a_write_under_the_items_own_dir_or_a_read_is_not_flagged(rec: dict) -> None:
    assert sg.loose_writes(_text([rec])) == []


def test_the_subagent_stop_hook_flags_a_planted_write(tmp_path: Path) -> None:
    """End to end: the planted `scratchpad/release.log` write reaches the session and the ledger."""
    sub = tmp_path / "proj" / "subagents"
    sub.mkdir(parents=True)
    t = sub / "agent-a1.jsonl"
    report = "Status: landed\nabc1234 x"
    t.write_text(_text([
        _use("Bash", command=f"tools/release.sh 1.2.3 > {PAD}/release.log 2>&1"),
        {"type": "assistant", "message": {"content": [{"type": "text", "text": report}]}},
    ]))  # fmt: skip
    event = {"session_id": "s", "hook_event_name": "SubagentStop", "agent_id": "a1",
             "agent_type": "repo-worker", "last_assistant_message": report,
             "agent_transcript_path": str(t)}  # fmt: skip
    cache = tmp_path / "cache"
    r = subprocess.run(
        [sys.executable, str(GUARD)], input=json.dumps(event), capture_output=True, text=True,
        env=hermetic_env(XDG_CACHE_HOME=str(cache)), timeout=30, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert f"{PAD}/release.log" in out["systemMessage"] and "loopctl" in out["systemMessage"]
    (line,) = [
        json.loads(x) for x in (cache / "goh" / "agent-spawns.jsonl").read_text().splitlines()
    ]
    assert line["scratch_loose"] == [f"{PAD}/release.log"]


@pytest.mark.parametrize("name", ["repo-worker.md", "repo-worker-ui.md"])
def test_both_agent_definitions_carry_the_scratch_and_release_rules(name: str) -> None:
    body = (AGENTS / name).read_text()
    assert "loopctl.py scratch <ITEM-ID> --base" in body and "--file NAME" in body
    assert "never a fixed name in the shared scratchpad" in body
    assert "GOH_SHIP_RELEASE" in body and "loopctl done" in body
