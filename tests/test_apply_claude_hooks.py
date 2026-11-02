"""goh registers its Claude Code hooks only in groups that are ALL goh's (GOH.SHAREDGRP, 2026-11-02).

THE CLASS. A PreToolUse group is `{matcher, hooks: [...]}`: the matcher belongs to the GROUP, so
widening it for one hook widens it for every hook in it. GOH.MONGUARD added `Monitor` to three
matchers by hand, and one of those groups also held `rtk hook claude`, which then started
rewriting Monitor commands -- its `updatedInput` carries only `command`, so Monitor's other fields
(`description`, `timeout_ms`, `persistent`) can be dropped. The invariant: goh creates or widens
only a group whose hooks are all goh's; a goh hook that shares a group with a foreign one is split
out into its own group FIRST, and the foreign group's matcher is left byte for byte as it was.

`scripts/apply_claude_hooks.py` is the one thing that writes goh's entries into settings.json.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env

SCRIPT = REPO_ROOT / "scripts" / "apply_claude_hooks.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import apply_claude_hooks as ach

GOH = 'python3 "${GOH_DIR:-$HOME/Projects/gates_of_heck}/hooks/claude/discard_guard.py" || exit 1'
GOH2 = 'python3 "${GOH_DIR:-$HOME/Projects/gates_of_heck}/hooks/claude/hold_guard.py" || exit 1'
RTK = "rtk hook claude"
NARROW = "Bash|Write|Edit|MultiEdit"
WIDE = "Bash|Monitor|Write|Edit|MultiEdit"


def _hook(cmd: str, timeout: int | None = 15) -> dict:
    return {"type": "command", "command": cmd, **({"timeout": timeout} if timeout else {})}


def _regs(*entries: tuple[str, str | None, str]) -> dict:
    return {"hooks": {ev: [{"matcher": m, "command": c, "timeout": 15}] for ev, m, c in entries}}


def _groups(settings: dict, event: str = "PreToolUse") -> list[dict]:
    return settings["hooks"][event]


def test_a_goh_hook_sharing_a_group_with_a_foreign_hook_is_split_before_it_is_widened() -> None:
    settings = {
        "hooks": {"PreToolUse": [{"matcher": NARROW, "hooks": [_hook(RTK, None), _hook(GOH)]}]}
    }
    out, notes = ach.apply(settings, _regs(("PreToolUse", WIDE, GOH)))
    foreign, own = _groups(out)
    assert foreign == {"matcher": NARROW, "hooks": [_hook(RTK, None)]}  # byte for byte what it was
    assert own == {"matcher": WIDE, "hooks": [_hook(GOH)]}
    assert any("split" in n for n in notes)


def test_the_foreign_matcher_never_widens_wherever_the_foreign_hook_sits() -> None:
    """The foreign hook is last in the group, and first: the split does not depend on order."""
    for hooks in ([_hook(GOH), _hook(RTK, None)], [_hook(RTK, None), _hook(GOH)]):
        out, _ = ach.apply({"hooks": {"PreToolUse": [{"matcher": NARROW, "hooks": hooks}]}},
                           _regs(("PreToolUse", WIDE, GOH)))  # fmt: skip
        for g in _groups(out):
            if any(h["command"] == RTK for h in g["hooks"]):
                assert g["matcher"] == NARROW and all(h["command"] == RTK for h in g["hooks"])


def test_a_group_of_only_goh_hooks_is_widened_in_place() -> None:
    settings = {"hooks": {"PreToolUse": [{"matcher": NARROW, "hooks": [_hook(GOH)]}]}}
    out, _ = ach.apply(settings, _regs(("PreToolUse", WIDE, GOH)))
    assert _groups(out) == [{"matcher": WIDE, "hooks": [_hook(GOH)]}]


def test_two_goh_hooks_that_want_different_matchers_do_not_share_one_group() -> None:
    settings = {"hooks": {"PreToolUse": [{"matcher": NARROW, "hooks": [_hook(GOH), _hook(GOH2)]}]}}
    regs = {"hooks": {"PreToolUse": [
        {"matcher": WIDE, "command": GOH, "timeout": 15},
        {"matcher": WIDE + "|NotebookEdit", "command": GOH2, "timeout": 15},
    ]}}  # fmt: skip
    out, _ = ach.apply(settings, regs)
    by_cmd = {g["hooks"][0]["command"]: g for g in _groups(out)}
    assert all(len(g["hooks"]) == 1 for g in _groups(out))
    assert by_cmd[GOH]["matcher"] == WIDE and by_cmd[GOH2]["matcher"].endswith("NotebookEdit")


def test_a_shared_group_whose_matcher_already_fits_is_left_alone() -> None:
    settings = {
        "hooks": {"PreToolUse": [{"matcher": WIDE, "hooks": [_hook(RTK, None), _hook(GOH)]}]}
    }
    out, notes = ach.apply(settings, _regs(("PreToolUse", WIDE, GOH)))
    assert out == settings and notes == []


def test_a_missing_goh_hook_gets_a_group_of_its_own_and_a_foreign_group_is_untouched() -> None:
    foreign = {"hooks": [{"type": "command", "command": "rex __internal session-start"}]}
    settings = {"hooks": {"SessionStart": [foreign]}}
    out, _ = ach.apply(settings, _regs(("SessionStart", None, GOH)))
    assert _groups(out, "SessionStart") == [foreign, {"hooks": [_hook(GOH)]}]  # no matcher key


def test_a_command_or_timeout_that_drifted_is_corrected_in_place() -> None:
    old = GOH.replace(" || exit 1", "")
    settings = {
        "hooks": {"PreToolUse": [{"matcher": WIDE, "hooks": [_hook(RTK, None), _hook(old, 5)]}]}
    }
    out, _ = ach.apply(settings, _regs(("PreToolUse", WIDE, GOH)))
    assert [h["command"] for h in _groups(out)[0]["hooks"]] == [RTK, GOH]
    assert _groups(out)[0]["hooks"][1]["timeout"] == 15


def test_applying_twice_is_a_no_op_and_the_input_is_never_mutated() -> None:
    settings = {
        "hooks": {"PreToolUse": [{"matcher": NARROW, "hooks": [_hook(RTK, None), _hook(GOH)]}]}
    }
    before = copy.deepcopy(settings)
    regs = _regs(("PreToolUse", WIDE, GOH))
    once, _ = ach.apply(settings, regs)
    twice, notes = ach.apply(once, regs)
    assert settings == before and twice == once and notes == []


def test_a_foreign_hook_is_never_mistaken_for_goh() -> None:
    assert not ach.is_goh(RTK)
    assert not ach.is_goh("/bin/sh -c 'rex __internal session-start --agent claude'")
    assert ach.is_goh(GOH)


def test_every_registration_applies_to_an_empty_settings_and_the_drift_check_agrees(
    tmp_path: Path,
) -> None:
    """The installer and the weekly drift check read the same file: what one writes, the other passes."""
    import model_choice_watch

    regs = json.loads((REPO_ROOT / "hooks" / "claude" / "registrations.json").read_text())
    out, _ = ach.apply({}, regs)
    assert ach.apply(out, regs) == (out, [])
    # round-trip through the drift check's own reader
    reg_path = REPO_ROOT / "hooks" / "claude" / "registrations.json"
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(out))
    drift = model_choice_watch.settings_drift(settings, reg_path)
    assert drift["red"] is False, drift


def _run(settings: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--settings", str(settings), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60, env=hermetic_env(), check=False,
    )  # fmt: skip


def test_the_cli_backs_up_before_it_writes_and_keeps_the_file_valid_json(tmp_path: Path) -> None:
    s = tmp_path / "settings.json"
    original = {"model": "opus", "hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [_hook(RTK, None)]}]}}  # fmt: skip
    s.write_text(json.dumps(original, indent=2) + "\n")
    r = _run(s)
    assert r.returncode == 0, r.stderr
    backups = list(tmp_path.glob("settings.json.goh-bak-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == original
    written = json.loads(s.read_text())
    assert written["model"] == "opus"
    assert _groups(written)[0] == original["hooks"]["PreToolUse"][0]  # rtk's group, as it was
    assert _run(s).returncode == 0
    assert len(list(tmp_path.glob("settings.json.goh-bak-*"))) == 1  # a no-op writes no backup


def test_the_cli_dry_run_prints_the_diff_and_writes_nothing(tmp_path: Path) -> None:
    s = tmp_path / "settings.json"
    s.write_text("{}\n")
    r = _run(s, "--dry-run")
    assert r.returncode == 0 and "discard_guard.py" in r.stdout
    assert s.read_text() == "{}\n" and not list(tmp_path.glob("*.goh-bak-*"))


def test_the_cli_refuses_a_settings_file_that_is_not_json_and_leaves_it(tmp_path: Path) -> None:
    s = tmp_path / "settings.json"
    s.write_text("{ not json")
    r = _run(s)
    assert r.returncode != 0 and s.read_text() == "{ not json"
    assert not list(tmp_path.glob("*.goh-bak-*"))


def test_the_cli_check_exits_1_when_settings_would_change(tmp_path: Path) -> None:
    s = tmp_path / "settings.json"
    s.write_text("{}\n")
    assert _run(s, "--check").returncode == 1 and s.read_text() == "{}\n"


def test_the_registrations_carry_every_script_hooks_claude_registers_in_the_real_settings() -> None:
    """estate_advisories.py is registered in ~/.claude/settings.json; the file of record had it not."""
    regs = json.loads((REPO_ROOT / "hooks" / "claude" / "registrations.json").read_text())["hooks"]
    assert any("estate_advisories.py" in e["command"] for e in regs["SessionStart"])


@pytest.mark.parametrize("event", ["PreToolUse"])
def test_the_real_registrations_never_ask_to_share_a_group(event: str) -> None:
    """Applied over a settings whose rtk group holds a goh hook, rtk's matcher does not move."""
    regs = json.loads((REPO_ROOT / "hooks" / "claude" / "registrations.json").read_text())
    discard = next(e for e in regs["hooks"][event] if "discard_guard" in e["command"])
    settings = {"hooks": {event: [{"matcher": NARROW, "hooks": [_hook(RTK, None),
                                                                 _hook(discard["command"])]}]}}  # fmt: skip
    out, _ = ach.apply(settings, regs)
    rtk = next(g for g in _groups(out, event) if any(h["command"] == RTK for h in g["hooks"]))
    assert rtk == {"matcher": NARROW, "hooks": [_hook(RTK, None)]}
