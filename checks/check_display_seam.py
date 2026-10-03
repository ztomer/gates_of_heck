#!/usr/bin/env python3
"""On-screen presentation routes through a declared seam, and a live harness says so out loud.

    check_display_seam.py --policy tools/display_seam_policy.json
    check_display_seam.py --policy tools/display_seam_policy.json --staged
    check_display_seam.py --probe            # prove this gate can go red

WHY THIS EXISTS, and it is NOT a copy of `check_no_screen_presentation.py`. That gate's rule is
"TEST TARGETS never touch the screen": it is handed the tests and refuses a screen API there.
This gate's rule is different and was written in `games/ZeroThunder`: **app source must not
present to the display except through the seam that owns it**, and the harness that DOES need the
screen must declare itself rather than be discovered by omission. One scopes a grep at tests, the
other scopes it at the program and exempts the seam -- which is why they are two gates, and why
merging them would have deleted a capability rather than unified one (SUPERSOTA R6).

THE THREE RULES, and each one has a shape its author did not imagine first:

  SWIFT   Every presentation OR INPUT call in app source is a call site that can put pixels on a
          user's display, take their keyboard with a permission prompt, or compete with them for
          the mouse -- during a unit-test run, a golden render, or an unattended loop. They route
          through the file the policy names as the seam, or carry a justified marker.
          INPUT counts because it is the same problem: reading the real pointer, installing a
          monitor or session tap, moving the cursor or posting synthetic events all fight the
          user for their own mouse.

  PYTHON  A harness that drives the REAL screen (`screencapture`, System-Events activation,
          launching the app binary) must CALL the live-tier helper, which is refused under the
          offscreen policy at runtime. Naming the helper in prose does NOT count: that bought a
          blanket exemption for a whole file, measured, from one sentence of docstring. The
          declaration must carry a reason that is neither missing nor a placeholder -- the live
          tier is ATTENDED, so every harness parked there is a test a human babysits forever.
          The EXECUTOR may sit on an earlier line than the command, because a multi-line
          `subprocess.run([...])` is what `ruff format` emits and what the repo's own harnesses
          are written as.

  SHELL   Every tracked shell script, wherever it lives. A legacy `run_*.sh` is one `bash` away
          from painting the desktop during a CI run, so the script references the offscreen guard
          its own policy names, or carries a justified marker.

EXIT CODES. 0 clean · 1 violations · 2 usage/config error. A policy that will not parse, a policy
carrying a key this checker does not know, a policy naming a seam that does not exist, a policy
excluding a file without saying why, and a full run that examined ZERO files are all exit 2 —
never a pass (docs/contracts.md 7). A typo'd policy key is a config error rather than a silently
disabled rule, which is the only safe direction for a file that decides what is allowed to reach a
display.

`--staged` reads THE INDEX via checks/_gitutil.py, never the worktree.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import content_bytes, listed_files, repo_root  # noqa: E402
from _marker_reason import is_justified  # noqa: E402

# ONE masker, both gates. `swift_code_only` blanks Swift comments AND string literals while
# preserving line count 1:1, so a doc comment explaining the rule is not a violation. The consumer's
# own gate never masked, because a line-based skip was enough over its roots; over a whole app
# source tree it is not, and importing beats carrying a second 80-line scanner that would drift.
from check_no_screen_presentation import swift_code_only  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, info, ok  # noqa: E402

POLICY_KEYS = {
    "scan",
    "seam",
    "exclude",
    "exclude_why",
    "live_helper",
    "shell_guard",
    "app_launch",
    "offscreen_flags",
    "markers",
    "shell_scripts",
    "note",
}
DEFAULTS = {
    "live_helper": "require_live_screen",
    "shell_guard": "GOH_HEADLESS",
    "markers": ["screen-ok:"],
    "shell_scripts": True,
}

# ── what counts as reaching the screen or taking the user's input ─────────────
SWIFT_PRESENTATION = (
    # On-screen presentation.
    r"\.(orderFront|orderFrontRegardless|makeKeyAndOrderFront|showWindow)\s*\("
    r"|NSApp\.activate\s*\(|NSApplication\.shared\.activate"
    r"|\.setActivationPolicy\s*\(|NSApplication\.shared\.run\s*\("
    r"|\.runModal\s*\("
    # A permission request is an on-screen act the app merely TRIGGERS: macOS raises the prompt,
    # gives it keyboard focus, and blocks the run until a human clicks.
    r"|AXIsProcessTrustedWithOptions\s*\(|CGRequestScreenCaptureAccess\s*\("
    r"|NSWorkspace\.shared\.open\s*\("
    # INPUT is the same problem as presentation.
    r"|NSEvent\.mouseLocation|NSEvent\.pressedMouseButtons"
    r"|NSEvent\.add(Global|Local)MonitorForEvents\s*\("
    # `CGEvent(` is NOT here: constructing a CGEvent to inject into the app's OWN handler
    # chain is what a unit test for an event tap does, and the consumer's gate has always
    # allowed it. What reaches the user's input stream is a POST or a TAP, named as such.
    r"|CGEvent\.tapCreate\s*\(|CGEvent\.post\s*\(|CGEventPost\s*\("
    r"|CGWarpMouseCursorPosition\s*\("
    r"|CGDisplayMoveCursorToPoint\s*\(|NSCursor\.(hide|unhide|setHiddenUntilMouseMoves)\s*\("
    r"|\.post\s*\(\s*tap:"
)

# ANY app automation drives the GUI, not just System Events. This pattern originally listed
# "System Events" and missed `tell application "Finder"` in a shell script — the gate passed
# because it could not see, which is the failure mode it exists to prevent.
LIVE_SCREEN = re.compile(
    r"screencapture|cliclick|osascript|System Events|tell application|to activate"
)
# ...but only when the line's STATEMENT executes something. Prose in a docstring explaining why a
# capture is unreliable is not a harness driving the screen, and flagging it trains people to
# ignore the gate.
EXECUTES = re.compile(r"subprocess|os\.system|Popen|check_output|check_call|run\(|\$\(|eval")
# How far back to look for the executor of a multi-line call. `ruff format` breaks a
# `subprocess.run([...])` across lines, so the executor is usually 1-3 lines ABOVE the command.
LOOKBACK = 4

# A CALL statement, not a mention. The check was once a raw substring over the whole file. Both
# patterns are TEMPLATES: the helper's name comes from the consumer's policy, so they are compiled
# per run rather than at import.
LIVE_CALL = r"^\s*{helper}\s*\("
# A declaration must state WHY, and the reason must be substantive.
STR = r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'"
LIVE_WHY = r"{helper}\s*\(\s*(?P<name>" + STR + r")\s*,\s*(?P<why>" + STR + r")"
PLACEHOLDER_WHY = re.compile(
    r"^[\"']?\s*(needs? the (real )?screen|live|tbd|todo|n/?a|because)\b", re.I
)
MIN_WHY_CHARS = 25

COMMENTS = {".swift": ("//",), ".py": ("#",), ".sh": ("#",)}


class Policy:
    """The consumer's policy, with every key validated. A key this checker does not know is an
    error, not a comment: a mistyped `seam` would otherwise read as "no seam is exempt" and the
    gate would go red on the seam itself, or — worse — a mistyped key that DISABLES a rule."""

    def __init__(self, raw, root):
        unknown = sorted(set(raw) - POLICY_KEYS)
        if unknown:
            raise ValueError(f"unknown policy key(s): {', '.join(unknown)}")
        self.note = str(raw.get("note", ""))
        self.live_helper = str(raw.get("live_helper", DEFAULTS["live_helper"]))
        self.shell_guard = str(raw.get("shell_guard", DEFAULTS["shell_guard"]))
        self.markers = tuple(str(m) for m in raw.get("markers", DEFAULTS["markers"]))
        self.shell_scripts = bool(raw.get("shell_scripts", DEFAULTS["shell_scripts"]))
        self.seam = tuple(str(p) for p in raw.get("seam", ()))
        self.exclude = tuple(str(p) for p in raw.get("exclude", ()))
        # An exemption that does not say WHY is the same defect as a bare `screen-ok:` marker, and
        # the consumer's own list shipped with a reason beside every entry. Carried over, because
        # "tests/e2e/cleanup.py is exempt" is not reviewable and "artifact cleanup only" is.
        whys = raw.get("exclude_why", {})
        if not isinstance(whys, dict):
            raise ValueError("exclude_why must be an object of path -> reason")
        unsaid = [p for p in self.exclude if not str(whys.get(p, "")).strip()]
        if unsaid:
            raise ValueError(f"excluded with no reason in exclude_why: {', '.join(unsaid)}")
        self.scan = tuple(str(p) for p in raw.get("scan", ()))
        self.app_launch = re.compile(str(raw["app_launch"])) if raw.get("app_launch") else None
        self.offscreen_flags = tuple(str(f) for f in raw.get("offscreen_flags", ()))
        missing = [p for p in self.seam if not os.path.isfile(os.path.join(root, p))]
        if missing:
            raise ValueError(f"the policy names a seam that does not exist: {', '.join(missing)}")

    def exempt(self, rel):
        return rel in self.seam or rel in self.exclude

    def silenced(self, lines, idx):
        """A marker on this line or the one above that carries a real reason."""
        for probe in (lines[idx], lines[idx - 1] if idx > 0 else ""):
            if any(is_justified(probe, marker) for marker in self.markers):
                return True
        return False


def in_scan(rel, policy):
    """Repo-relative `rel` is in scope. No `scan` means the whole tree."""
    if not policy.scan:
        return True
    return any(rel == root or rel.startswith(root.rstrip("/") + "/") for root in policy.scan)


def scan_lines(text):
    """(lines, comment prefixes) — the shared shape every pass below iterates."""
    return text.split("\n")


def check_swift(rel, text, policy, out):
    lines = text.split("\n")
    # Patterns match the MASKED lines; markers are looked up on the ORIGINAL lines, which the
    # mask preserves 1:1.
    code = swift_code_only(text).split("\n")
    for i, line in enumerate(code):
        stripped = line.strip()
        if stripped.startswith(COMMENTS[".swift"]) or not stripped:
            continue
        if policy.silenced(lines, i):
            continue
        if re.search(SWIFT_PRESENTATION, line):
            out.append(
                (rel, i + 1, "reaches the screen or the user's input; route it through the seam")
            )
            return


def check_python(rel, text, policy, out):
    helper = re.escape(policy.live_helper)
    lines = scan_lines(text)
    call = re.search(LIVE_CALL.format(helper=helper), text, re.M)
    if call:
        # Declared live — but the declaration has to carry a real justification.
        whys = list(re.finditer(LIVE_WHY.format(helper=helper), text, re.S))
        if not whys:
            out.append(
                (
                    rel,
                    text.count("\n", 0, call.start()) + 1,
                    f"{policy.live_helper}() with no `why` reason",
                )
            )
            return
        for m in whys:
            why = m.group("why").strip().strip("\"'")
            if len(why) < MIN_WHY_CHARS or PLACEHOLDER_WHY.match(why):
                out.append(
                    (
                        rel,
                        text.count("\n", 0, m.start()) + 1,
                        f"live-tier reason is a placeholder: {why[:60]}",
                    )
                )
                return
        return  # a declared harness is the point of the helper, not a violation
    launches = bool(policy.app_launch and policy.app_launch.search(text)) and not any(
        flag in text for flag in policy.offscreen_flags
    )
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(COMMENTS[".py"]):
            continue
        if policy.silenced(lines, i):
            continue
        near = "\n".join(lines[max(0, i - LOOKBACK) : i + 1])
        hit = (LIVE_SCREEN.search(line) and EXECUTES.search(near)) or (
            launches and policy.app_launch.search(line)
        )
        if hit:
            out.append(
                (rel, i + 1, f"live-screen harness without {policy.live_helper}(): {stripped}")
            )
            return


def check_shell(rel, text, policy, out):
    """A shell harness cannot import the helper, so the contract is a one-line guard naming the
    offscreen variable its policy declares."""
    lines = scan_lines(text)
    if policy.shell_guard in text:
        return
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(COMMENTS[".sh"]):
            continue
        if policy.silenced(lines, i):
            continue
        if LIVE_SCREEN.search(line):
            out.append(
                (
                    rel,
                    i + 1,
                    f"live-screen shell script with no {policy.shell_guard} guard: {stripped}",
                )
            )
            return


def targets(root, staged, policy):
    """Repo-root-relative files to examine. The shell pass is deliberately NOT scoped: a live-screen
    script is a screen problem wherever it lives, and a gate scoped to where you expect the
    problem only finds the problems you expected."""
    out = []
    for rel in listed_files(root, staged=staged):
        ext = os.path.splitext(rel)[1]
        if policy.exempt(rel):
            continue
        if ext == ".sh":
            if policy.shell_scripts:
                out.append(rel)
        elif ext in (".swift", ".py") and in_scan(rel, policy):
            out.append(rel)
    return sorted(out)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--policy", default=None, help="JSON policy file (required unless --probe)")
    ap.add_argument("--staged", action="store_true", help="police the INDEX, not the worktree")
    ap.add_argument("--probe", action="store_true", help="prove this gate can go red")
    args = ap.parse_args(argv)
    if args.probe:
        # The self-proof lives beside this file so neither crowds the cap; it drives main() from
        # here rather than reimplementing the rules, so a probe cannot pass on a stale copy.
        from _display_seam_probe import probe

        return probe()
    if not args.policy:
        ap.error("--policy is required: this gate has no opinion about a consumer's display")

    root = repo_root() or os.getcwd()
    try:
        with open(args.policy, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        err(f"[no_display_seam] cannot read the policy at {args.policy}: {exc}")
        info("A policy that will not parse is not a policy. Fix it, or the gate is not running.")
        return 2
    try:
        policy = Policy(raw if isinstance(raw, dict) else {}, root)
    except ValueError as exc:
        err(f"[no_display_seam] {args.policy}: {exc}")
        return 2

    bad, checked = [], 0
    for rel in targets(root, args.staged, policy):
        blob = content_bytes(root, rel, staged=args.staged)
        if blob is None or b"\0" in blob[:8000]:
            continue
        checked += 1
        text = blob.decode("utf-8", "replace")
        ext = os.path.splitext(rel)[1]
        if ext == ".swift":
            check_swift(rel, text, policy, bad)
        elif ext == ".py":
            check_python(rel, text, policy, bad)
        else:
            check_shell(rel, text, policy, bad)

    if checked == 0 and not args.staged:
        err(f"[no_display_seam] examined ZERO files — refusing to report a pass for nothing")
        info(
            f"The policy's `scan` roots matched no tracked source: {', '.join(policy.scan) or '(whole tree)'}"
        )
        info(
            "A gate that covered nothing prints exactly what a gate that covered everything prints."
        )
        return 2

    if bad:
        err(f"[no_display_seam] {len(bad)} screen/input call site(s) outside the seam:")
        for rel, line_no, why in bad:
            err(f"    {rel}:{line_no}: {why}")
        info(f"  Route through the seam ({', '.join(policy.seam) or 'none declared'}), or mark the")
        info(
            f"  line `{'` / `'.join(policy.markers)}`: <reason>. A marker with no reason is not one."
        )
        if policy.live_helper:
            info(f"  A harness that needs the real screen calls {policy.live_helper}(NAME, WHY).")
        return 1
    if checked == 0:
        # Named, not "OK — 0 files". A step that did not run prints what a step that passed
        # prints, and that is the whole of R4: the reader cannot tell the two apart from the
        # scrollback.
        ok("[no_display_seam] nothing staged — the index holds no source to examine")
        return 0
    ok(f"[no_display_seam] OK — {checked} file(s), nothing reaches the display outside the seam")
    return 0


if __name__ == "__main__":
    sys.exit(main())
