"""The skills corpus is judged when a SKILL is written, not only when it is committed (C2).

`goh skills` gates the corpus at commit and push; a writer who breaks it (no frontmatter, a
SKILL.md over the word ceiling) heard about it only there, sessions later, from a gate that names
a file the writer no longer has open. `hooks/claude/skill_edit.sh` is a Claude Code PostToolUse
hook (`~/.claude/settings.json`): an edit inside the corpus runs `goh skills` on it at once, and a
finding is exit 2 with the report on stderr -- the channel Claude Code hands back to the writer.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

HOOK = REPO_ROOT / "hooks" / "claude" / "skill_edit.sh"
SKILL = "---\nname: skill-{i}\ndescription: does a thing worth triggering on.\n---\n\n# s\n\nbody\n"


def _corpus(root: Path) -> Path:
    for i in range(6):
        (root / f"skill-{i}").mkdir(parents=True)
        (root / f"skill-{i}" / "SKILL.md").write_text(SKILL.format(i=i))
    return root


def _fire(goh: Path, corpus: Path, edited: Path, tool: str = "Edit"):
    event = {
        "tool_name": tool,
        "tool_input": {"file_path": str(edited)},
        "hook_event_name": "PostToolUse",
    }
    return subprocess.run(
        ["/bin/bash", str(HOOK)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_BIN=str(goh), GOH_SKILLS_ROOT=str(corpus)),
        timeout=60,
    )


def test_a_broken_skill_is_reported_to_its_writer(goh: Path, tmp_path: Path) -> None:
    corpus = _corpus(tmp_path / "skills")
    bad = corpus / "skill-3" / "SKILL.md"
    bad.write_text("no frontmatter at all\n")
    r = _fire(goh, corpus, bad)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "skill-3" in r.stderr, r.stderr


def test_a_clean_corpus_is_silent(goh: Path, tmp_path: Path) -> None:
    corpus = _corpus(tmp_path / "skills")
    r = _fire(goh, corpus, corpus / "skill-1" / "SKILL.md")
    assert (r.returncode, r.stderr) == (0, ""), r.stdout + r.stderr


def test_an_edit_outside_the_corpus_never_runs_the_check(goh: Path, tmp_path: Path) -> None:
    corpus = _corpus(tmp_path / "skills")
    (corpus / "skill-3" / "SKILL.md").write_text("no frontmatter at all\n")  # broken, but not ours
    elsewhere = tmp_path / "repo" / "SKILL.md"
    elsewhere.parent.mkdir()
    elsewhere.write_text("x\n")
    r = _fire(goh, corpus, elsewhere)
    assert (r.returncode, r.stderr) == (0, ""), r.stdout + r.stderr


def test_a_reference_file_inside_a_skill_counts_as_the_corpus(goh: Path, tmp_path: Path) -> None:
    corpus = _corpus(tmp_path / "skills")
    (corpus / "skill-2" / "SKILL.md").write_text("no frontmatter at all\n")
    ref = corpus / "skill-2" / "references" / "notes.md"
    ref.parent.mkdir()
    ref.write_text("notes\n")
    assert _fire(goh, corpus, ref, tool="Write").returncode == 2


def test_an_event_with_no_file_is_ignored(goh: Path, tmp_path: Path) -> None:
    corpus = _corpus(tmp_path / "skills")
    r = subprocess.run(
        ["/bin/bash", str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}),
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_BIN=str(goh), GOH_SKILLS_ROOT=str(corpus)),
        timeout=60,
    )
    assert (r.returncode, r.stderr) == (0, ""), r.stderr


def test_the_installed_settings_point_at_this_hook() -> None:
    """The hook is wired where Claude Code reads it, or it is a gate nobody runs."""
    settings = Path.home() / ".claude" / "settings.json"
    if not settings.exists():
        import pytest

        pytest.skip("no ~/.claude/settings.json on this machine")
    hooks = json.loads(settings.read_text()).get("hooks", {}).get("PostToolUse", [])
    commands = [h.get("command", "") for entry in hooks for h in entry.get("hooks", [])]
    assert any("hooks/claude/skill_edit.sh" in c for c in commands), commands
