"""`check_display_seam.py` — the seam gate, its policy handling, and its own calibration.

The gate came out of `games/ZeroThunder/tools/check_no_screen_presentation.py` (SUPERSOTA R5/R6):
that repo carried a repo-local checker for a rule this repo had no gate for at all — *app source
must not reach the display except through a declared seam* — while `check_no_screen_presentation.py`
answers a different question about test sources. So the capability moved here, the consumer's
policy became a JSON file, and the copy was deleted.

These tests pin three things: the gate's rules on fixtures, its refusal to honour a policy it
cannot, and — case by case — that dropping any ONE rule turns its own probe row red. The last is
the load-bearing one. A gate with 22 rules and a probe that only exercises 3 of them is a gate
that reports 19 rules it has never seen work.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import REPO_ROOT as ROOT
from _fast_git import fast_init  # noqa: E402

CHECK = ROOT / "checks" / "check_display_seam.py"
PROBE = ROOT / "checks" / "_display_seam_probe.py"
MARKER = ROOT / "checks" / "_marker_reason.py"

sys.path.insert(0, str(ROOT / "checks"))


def run_checker(root, *args):
    return subprocess.run(
        [sys.executable, str(CHECK), *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    ).returncode


def estate(tmp_path, files, policy=None):
    """A git repo holding `files` plus a policy naming Core/DisplayPolicy.swift as the seam."""
    root = tmp_path / "repo"
    (root / "Core").mkdir(parents=True, exist_ok=True)
    for rel, body in {
        **files,
        "Core/DisplayPolicy.swift": "func p() { w.orderFront(nil) }\n",
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    merged = {
        "seam": ["Core/DisplayPolicy.swift"],
        "markers": ["screen-ok:", "input-ok:"],
        "shell_guard": "GOH_HEADLESS",
        "live_helper": "require_live_screen",
    }
    merged.update(policy or {})
    (root / "policy.json").write_text(json.dumps(merged), encoding="utf-8")
    fast_init(root)
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    return root


def test_the_probe_passes():
    got = subprocess.run(
        [sys.executable, str(CHECK), "--probe"], capture_output=True, text=True, check=False
    )
    assert got.returncode == 0, got.stdout + got.stderr


def test_the_marker_reason_cases_pass():
    got = subprocess.run([sys.executable, str(MARKER)], capture_output=True, text=True, check=False)
    assert got.returncode == 0, got.stdout + got.stderr


def test_the_gate_refuses_to_run_without_a_policy(tmp_path):
    root = estate(tmp_path, {"Core/quiet.swift": "func f() {}\n"})
    got = run_checker(root)
    assert got.returncode == 2
    assert "--policy is required" in got.stderr


def test_a_policy_with_an_unknown_key_is_refused_not_ignored(tmp_path):
    """A mistyped `seam` must not read as "no seam is exempt", and a mistyped rule must not read
    as "that rule is off"."""
    root = estate(tmp_path, {"Core/quiet.swift": "func f() {}\n"}, {"scean": ["Core"]})
    got = run_checker(root, "--policy", "policy.json")
    assert got.returncode == 2
    assert "unknown policy key" in got.stderr


def test_a_policy_naming_a_seam_that_does_not_exist_is_refused(tmp_path):
    root = estate(tmp_path, {"Core/quiet.swift": "func f() {}\n"}, {"seam": ["Core/Nope.swift"]})
    got = run_checker(root, "--policy", "policy.json")
    assert got.returncode == 2
    assert "does not exist" in got.stderr


def test_a_policy_that_is_not_json_is_refused(tmp_path):
    root = estate(tmp_path, {"Core/quiet.swift": "func f() {}\n"})
    (root / "policy.json").write_text("{ not json", encoding="utf-8")
    got = run_checker(root, "--policy", "policy.json")
    assert got.returncode == 2


def test_an_exclusion_without_a_reason_is_refused(tmp_path):
    """The same rule as a bare `screen-ok:` marker, applied to the exemption list: an entry that
    does not say why is not reviewable, and the consumer's own list carried a reason per entry."""
    root = estate(
        tmp_path, {"Core/quiet.swift": "func f() {}\n"}, {"exclude": ["Core/quiet.swift"]}
    )
    got = run_checker(root, "--policy", "policy.json")
    assert got.returncode == 2
    assert "excluded with no reason" in got.stderr


def test_nothing_staged_is_NAMED_not_reported_as_a_pass_over_zero_files(tmp_path):
    """R4: a step that did not run prints what a step that passed prints. An empty INDEX is
    normal; an empty TREE is a refusal. They must not read alike."""
    root = estate(tmp_path, {"Core/quiet.swift": "func f() {}\n"})
    _git(root, "commit", "-qm", "fixture")  # an empty INDEX, not an empty worktree
    got = run_checker(root, "--policy", "policy.json", "--staged")
    assert got.returncode == 0
    assert "nothing staged" in got.stdout
    assert "0 file(s)" not in got.stdout


def test_a_run_that_examines_nothing_is_refused(tmp_path):
    """Zero measurable files is never a silent pass (docs/contracts.md 7)."""
    root = estate(tmp_path, {"Core/quiet.swift": "func f() {}\n"}, {"scan": ["nowhere"]})
    got = run_checker(root, "--policy", "policy.json")
    assert got.returncode == 2
    assert "ZERO files" in got.stderr


def test_staged_policies_the_index_not_the_worktree(tmp_path):
    root = estate(tmp_path, {"Core/quiet.swift": "func f() {}\n"})
    (root / "UI").mkdir()
    # HEAD holds the CLEAN file. Committing the violation would make the index and HEAD agree,
    # which leaves nothing staged to police — a fixture that cannot fail (SUPERSOTA R3).
    (root / "UI" / "win.swift").write_text("func f() {}\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture")
    # Violating in the worktree only: a staged run must be GREEN.
    (root / "UI" / "win.swift").write_text("func f() { w.orderFrontRegardless() }\n", "utf-8")
    assert run_checker(root, "--policy", "policy.json", "--staged").returncode == 0
    # Violating in the INDEX, clean in the worktree: the staged run must be RED.
    _git(root, "add", "UI/win.swift")
    (root / "UI" / "win.swift").write_text("func f() {}\n", encoding="utf-8")
    got = run_checker(root, "--policy", "policy.json", "--staged")
    assert got.returncode == 1, got.stdout + got.stderr
    assert "UI/win.swift:1" in got.stderr
    # ...and a full run measures the WORKTREE, which is clean.
    assert run_checker(root, "--policy", "policy.json").returncode == 0


# ── the calibration: drop one rule, watch the probe's own row for it go red ────

RULES = (
    (
        "swift-input-family",
        'r"|NSEvent\\.mouseLocation|NSEvent\\.pressedMouseButtons"',
        'r"|NSEventX"',
    ),
    (
        "swift-tap-family",
        'r"|CGEvent\\.tapCreate\\s*\\(|CGEvent\\.post\\s*\\(|CGEventPost\\s*\\("',
        'r"|CGEventX"',
    ),
    (
        "swift-cursor",
        'r"|CGDisplayMoveCursorToPoint\\s*\\(|NSCursor\\.(hide|unhide|setHiddenUntilMouseMoves)\\s*\\("',
        'r"|NSCursorX"',
    ),
    (
        "swift-window",
        'r"\\.(orderFront|orderFrontRegardless|makeKeyAndOrderFront|showWindow)\\s*\\("',
        'r"\\.(orderFrontX)\\s*\\("',
    ),
    (
        "swift-permission",
        'r"|AXIsProcessTrustedWithOptions\\s*\\(|CGRequestScreenCaptureAccess\\s*\\("',
        'r"|AXIsX"',
    ),
    ("swift-system-pane", 'r"|NSWorkspace\\.shared\\.open\\s*\\("', 'r"|NSWorkspaceX"'),
    ("masking", 'code = swift_code_only(text).split("\\n")', 'code = text.split("\\n")'),
    (
        "justified-marker",
        "if any(is_justified(probe, marker) for marker in self.markers):",
        "if any(marker in probe for marker in self.markers):",
    ),
    (
        "marker-above",
        'for probe in (lines[idx], lines[idx - 1] if idx > 0 else ""):',
        "for probe in (lines[idx],):",
    ),
    ("lookback", "LOOKBACK = 4", "LOOKBACK = 0"),
    ("helper-must-be-called", 'LIVE_CALL = r"^\\s*{helper}\\s*\\("', 'LIVE_CALL = r"{helper}"'),
    ("live-reason", "if len(why) < MIN_WHY_CHARS or PLACEHOLDER_WHY.match(why):", "if False:"),
    ("seam-exempt", "return rel in self.seam or rel in self.exclude", "return rel in self.exclude"),
    (
        "exclude-honoured",
        "return rel in self.seam or rel in self.exclude",
        "return rel in self.seam",
    ),
    ("shell-guard", "if policy.shell_guard in text:", "if False:"),
    ("shell-pass-global", 'if ext == ".sh":', "if False:"),
    (
        "declared-exempt",
        "return  # a declared harness is the point",
        "pass  # a declared harness is the point",
    ),
    (
        "offscreen-declared",
        'offscreen_flags = tuple(str(f) for f in raw.get("offscreen_flags", ()))',
        'offscreen_flags = ("--golden-out", "--offscreen-live")',
    ),
    (
        "app-launch",
        "and not any(\n        flag in text for flag in policy.offscreen_flags\n    )",
        "and False and not any(\n        flag in text for flag in policy.offscreen_flags\n    )",
    ),
    ("unknown-key-refused", "if unknown:", "if False:"),
    (
        "exclusion-why-refused",
        "        if unsaid:",
        "        if False:",
    ),
    ("missing-seam-refused", "if missing:", "if False:"),
    ("zero-scope-refused", "if checked == 0 and not args.staged:", "if False:"),
)


@pytest.mark.parametrize(("label", "old", "new"), RULES, ids=[r[0] for r in RULES])
def test_the_probe_goes_red_when_one_rule_is_dropped(tmp_path, label, old, new):
    """One neutered rule, the real module copied beside its real self-proof, and the self-proof
    must notice. Subprocessed so the neutering cannot leak into the rest of the suite."""
    source = CHECK.read_text(encoding="utf-8")
    assert old in source, f"the rule this case drops is not in the gate: {old!r}"
    work = tmp_path / "checks"
    work.mkdir()
    (work / "check_display_seam.py").write_text(source.replace(old, new), encoding="utf-8")
    for name in (
        "_display_seam_probe.py",
        "_marker_reason.py",
        "_gitutil.py",
        "_swift_text.py",
    ):
        (work / name).write_text((ROOT / "checks" / name).read_text(encoding="utf-8"), "utf-8")
    got = subprocess.run(
        [sys.executable, str(work / "check_display_seam.py"), "--probe"],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(ROOT)),
    )
    assert got.returncode == 1, f"the probe stayed green without the {label} rule:\n{got.stdout}"
    assert "wrong" in got.stderr, got.stderr
