"""A GOH_DIR naming a REMOVED HEAD export still finds gates_of_heck (GOH.TRANSIENTDIR).

Every gate entry point re-runs from `~/.cache/goh/head/<sha>` and exports GOH_DIR at it
(gates/_from_head.sh). A long-lived process started under a gate kept that value: a ship's
release kit `open`ed Routines.app with it, and the routines daemon's from_head jobs exited 2 from
2026-11-12 20:43 once the export was pruned (the disk-pressure reclaim never ran); a Claude
session carried another, and every commit from it was refused by pre-commit ("no gates_of_heck
commit gate at .../head/c06bc794..."). Two halves, both tested here:

  * the escape: the release kit's launch hands the app launchd's login environment, not its own;
  * the readers: a GOH_DIR that is not a directory resolves to GOH_LIVE_ROOT, then the default
    checkout, through ONE rule (lib/goh_root.sh; copied verbatim into each stock hook, which
    cannot source anything; lib/goh_root.py; the native binary's `goh_root`).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env, native_goh_path

HOOKS = {
    "pre-commit": "commit_gate.sh",
    "pre-push": "push_gate.sh",
    "commit-msg": "commit_msg.sh",
    "post-rewrite": "sequencer_gate.sh",
    "pre-applypatch": "sequencer_gate.sh",
}
RUNNER = 'echo "ran $0 GOH_DIR=${GOH_DIR:-}" > "$(dirname "$0")/../ran"\n'


def _fake_goh(root: Path) -> Path:
    """A gates_of_heck with each hook's runner: it records which copy ran and the GOH_DIR it saw."""
    (root / "gates").mkdir(parents=True)
    for runner in set(HOOKS.values()):
        (root / "gates" / runner).write_text("#!/usr/bin/env bash\n" + RUNNER)
    (root / "gates" / "_git_env.sh").write_text("goh_hook_unbind() { :; }\n")
    return root


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True, stdin=subprocess.DEVNULL)
    (path / "msg").write_text("x\n")
    return path


@pytest.mark.parametrize("via", ["GOH_LIVE_ROOT", "the default checkout"])
@pytest.mark.parametrize("hook", sorted(HOOKS))
def test_a_stock_hook_runs_under_a_dead_export(tmp_path, hook, via):
    live = _fake_goh(tmp_path / "home" / "Projects" / "gates_of_heck")
    if via == "GOH_LIVE_ROOT":
        live = _fake_goh(tmp_path / "live")
    repo = _git_repo(tmp_path / "repo")
    dead = tmp_path / "cache" / "goh" / "head" / ("c06bc794" * 5)
    env = hermetic_env(drop_git=True, HOME=str(tmp_path / "home"), GOH_DIR=str(dead))
    env.pop("GOH_LIVE", None)
    if via == "GOH_LIVE_ROOT":
        env["GOH_LIVE_ROOT"] = str(live)
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "hooks" / hook), "msg"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    ran = (live / "ran").read_text()
    assert ran == f"ran {live}/gates/{HOOKS[hook]} GOH_DIR={live}\n", ran
    assert str(dead) in r.stderr, "the fallback is silent: nobody learns the process is stale"


def _function(text: str) -> str:
    m = re.search(r"^goh_dir_repair\(\) \{\n.*?^\}\n", text, re.DOTALL | re.MULTILINE)
    assert m, "no goh_dir_repair() in it"
    return m.group(0)


def test_every_stock_hook_carries_the_one_rule_verbatim():
    """Hooks are COPIED into each repo and source nothing before they find goh, so each carries
    the rule; this keeps every copy the lib's, so the rule has one definition."""
    rule = _function((REPO_ROOT / "lib" / "goh_root.sh").read_text())
    for hook in HOOKS:
        body = (REPO_ROOT / "hooks" / hook).read_text()
        assert _function(body) == rule, f"hooks/{hook} drifted from lib/goh_root.sh"
        assert body.index("goh_dir_repair\n") < body.index('GOH="${GOH_DIR'), hook


def test_a_live_goh_dir_is_left_alone(tmp_path):
    live = _fake_goh(tmp_path / "live")
    other = _fake_goh(tmp_path / "other")
    env = hermetic_env(GOH_DIR=str(other), GOH_LIVE_ROOT=str(live), HOME=str(tmp_path))
    r = subprocess.run(
        ["bash", "-c", f'. "{REPO_ROOT}/lib/goh_root.sh"; goh_dir_repair; echo "$GOH_DIR"'],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert (r.returncode, r.stdout.strip(), r.stderr) == (0, str(other), "")


def test_the_python_reader_resolves_a_dead_export(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    try:
        import goh_root
        import repo_hold
    finally:
        sys.path.pop(0)
    live = _fake_goh(tmp_path / "live")
    monkeypatch.setenv("GOH_DIR", str(tmp_path / "gone"))
    monkeypatch.setenv("GOH_LIVE_ROOT", str(live))
    assert goh_root.goh_root() == str(live)
    hold = repo_hold.Hold(root="/r", key="archived", value="why")
    assert hold.lift().startswith(f'"{live}/scripts/'), hold.lift()


def test_the_native_binary_runs_under_a_dead_export(tmp_path):
    """`goh agent-stalls` reads `$GOH_DIR/lib/house_python.sh`: under a dead export it found
    none. The binary re-runs itself with GOH_DIR repaired, so what it starts sees the live one."""
    env = hermetic_env(GOH_DIR=str(tmp_path / "gone"), GOH_LIVE_ROOT=str(REPO_ROOT))
    r = subprocess.run(
        [str(native_goh_path()), "agent-stalls", "--help"],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert str(tmp_path / "gone") in r.stderr, r.stderr


def test_the_release_kit_launches_the_app_without_its_environment(tmp_path):
    """The escape itself: the kit's `open` handed the app the ship's GOH_DIR, and the app (and
    everything it ran) kept it after the export was pruned. The opener runs with an empty
    environment, as `goh launch-app` does (its probe: only `env -i /usr/bin/open` matched the
    Finder's launch)."""
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    try:
        from test_release_kit_update_dev import run_update_dev
    finally:
        sys.path.pop(0)
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    (fakes / "open").write_text(f'#!/bin/bash\n/usr/bin/env > "{tmp_path}/app.env"\n')
    (fakes / "open").chmod(0o755)
    os.environ["GOH_TRANSIENTDIR_SENTINEL"] = "leaked"
    try:
        r = run_update_dev(tmp_path, fakes, "--launch")
    finally:
        os.environ.pop("GOH_TRANSIENTDIR_SENTINEL")
    assert r.returncode == 0, r.stdout + r.stderr
    seen = (tmp_path / "app.env").read_text()
    assert "GOH_TRANSIENTDIR_SENTINEL" not in seen and "GOH_DIR=" not in seen, seen
