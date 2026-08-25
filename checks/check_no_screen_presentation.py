#!/usr/bin/env python3
"""Static half of the "tests never render to the user's display" invariant.

A test that calls orderFrontRegardless / makeKeyAndOrderFront / pyautogui
flashes a real window over whatever the user is doing, needs a live
WindowServer (so the suite cannot run headless, over ssh, or locked), and
what it usually asserts is a property of the window SERVER, not of the code
under test. Three repos grew their own version of this gate and each only saw
the languages it happened to contain; this is the union, generalized.

Scans TEST TARGETS — paths given as arguments, or tracked files matched by
--scope — for per-language presentation APIs:

  .swift    NSWindow presentation (orderFront*, makeKeyAndOrderFront,
            showWindow), app activation (NSApp.activate, setActivationPolicy,
            runModal), real-display geometry (NSScreen, CGDisplay*), and the
            permission prompts macOS raises on the app's behalf
            (AXIsProcessTrustedWithOptions, CGRequestScreenCaptureAccess).
  .m/.mm    the Objective-C set: orderFront:/makeKeyAndOrderFront:/orderFront
            Regardless, activateIgnoringOtherApps, runModal, plus real-input
            synthesis (CGEventPost, CGWarpMouseCursorPosition) and
            screencapture.
  .py       pyautogui in any form, and subprocess/os.system/Popen executing a
            live-screen command (screencapture, cliclick, osascript, "tell
            application", open of an .app) — UNLESS the file participates in
            the headless contract by assigning GOH_HEADLESS for its children
            (the runtime half, lib/headless_env.sh).

Escape hatch: `# screen-ok: <reason>` or `// screen-ok: <reason>` on the
offending line or the one above it. Reviewed like code — say WHY.

Swift TYPE positions (`[NSScreen]`, `NSScreen?`, `-> NSScreen`, `is/as?
NSScreen`, `Foo<NSScreen>`, `x: [NSScreen]`) on opted-in bare-type patterns
are exempt: a fake's protocol surface NAMES these types without reading the
live display; member-access uses still flag.

    check_no_screen_presentation.py tests/ tools/render_check.sh
    check_no_screen_presentation.py --scope 'tests/**/*.swift'
    check_no_screen_presentation.py --staged --scope 'tests/*'

In --staged mode this polices THE INDEX via checks/_gitutil.py.

Exit codes: 0 clean · 1 violations · 2 usage error (no targets given).
"""

import argparse
import fnmatch
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import content_bytes, listed_files, repo_root  # noqa: E402

ALLOW_MARKER = "screen-ok:"

# The runtime half's contract variable. A Python harness that assigns it for
# its children has declared HOW its launches stay offscreen; policing those
# children's env is lib/headless_env.sh's job, not a grep's. Matches both
# `GOH_HEADLESS = ...` and the dict form `env["GOH_HEADLESS"] = ...`.
PY_GUARD = re.compile(r"GOH_HEADLESS[^\n=]*=")

# Each entry carries WHY it reaches the screen, so the finding says what is
# wrong rather than just what matched. A third element `True` marks a pattern
# whose occurrences may sit in a Swift TYPE position (see
# _in_swift_type_position): a fake's protocol surface TYPES `[NSScreen]` /
# `-> NSScreen` / `is NSScreen` without ever reading the live display, so an
# occurrence in a type shape is exempted while any member-access USE still
# flags.
SWIFT_PATTERNS = [
    (r"\.(orderFront|orderFrontRegardless|makeKeyAndOrderFront|showWindow)\s*\(",
     "puts a window on the user's display"),
    (r"\bNSApp\.activate\s*\(|\bNSApplication\.shared\.activate",
     "steals the user's focus"),
    (r"\.setActivationPolicy\s*\(", "changes how the process presents to the window server"),
    (r"\.runModal\s*\(", "runs a modal loop needing a live WindowServer"),
    (r"\bNSScreen\b", "reads the real display's geometry", True),
    (r"\bCGDisplay\w*\s*\(|\bCGMainDisplayID\b", "talks to a real display"),
    (r"\bscreencapture\b", "shells out to the screen capture tool"),
    (r"\bAXIsProcessTrustedWithOptions\s*\(|\bCGRequestScreenCaptureAccess\s*\(",
     "triggers a system permission prompt that takes the user's keyboard"),
    # ── absorbed from necrohand tools/check_headless_tests.py (2026-08-25) ──
    # A capitalized identifier ENDING in Window/OverlayView being CONSTRUCTED:
    # custom window/presentation-view classes present for real, exactly like
    # NSWindow( does. The capital anchor keeps lowercase helpers (makeWindow-,
    # updateWindow-style) out of the blast radius.
    (r"\b[A-Z]\w*(?:Window|OverlayView)\s*\(",
     "constructs a window-server window or presentation view directly"),
    (r"render:\s*\.presenting\b",
     "asks for the live presentation path instead of an offscreen render"),
    (r"\bSCStream\b|\bSCShareableContent\b|\bSCScreenshotManager\b|\bSCContentSharing\b",
     "captures the real screen via ScreenCaptureKit (needs a TCC grant)", True),
    (r"\bCGWindowList\w*\b", "reads the real window list", True),
    (r"\bCAMetalLayer\b", "creates/acquires a window-server drawable surface", True),
    (r"\bCAMetalLayer\s*\(|\bnextDrawable\s*\(",
     "creates/acquires a window-server drawable surface"),
    (r"\bCGEvent\w*\s*\(|\bCGWarpMouseCursorPosition\b",
     "posts real input to the whole machine"),
    (r"\bNSCursor\b", "moves or hides the user's real cursor", True),
    (r"\bNSApplication\.shared\b|\bNSApp\b",
     "starts or queries the shared application object"),
]

OBJC_PATTERNS = [
    (r"\borderFront:|\bmakeKeyAndOrderFront:|\borderFrontRegardless\b",
     "puts a window on the user's display"),
    (r"\bactivateIgnoringOtherApps\b|\bNSApp\s+activate\b",
     "steals the user's focus"),
    (r"\brunModal\b", "runs a modal loop needing a live WindowServer"),
    (r"\bCGEventPost\b|\bCGWarpMouseCursorPosition\b",
     "drives the user's real mouse/keyboard"),
    (r"\bscreencapture\b", "grabs the real display"),
]

# A .py violation comes in two shapes: pyautogui IS the harness by itself,
# while a live-screen COMMAND only counts when something actually EXECUTES it
# — prose mentioning screencapture must stay green, or the gate trains people
# to ignore it.
PY_EXECUTES = re.compile(r"subprocess|os\.system|Popen|check_output|check_call")
PY_LIVE_CMDS = re.compile(
    r"screencapture|cliclick|osascript|System Events|tell application"
    r"|\bopen\s+[^\"']*\.(app|bundle)\b"
)
PYTHON_RULES = [
    (re.compile(r"\bpyautogui\b"), "pyautogui drives the real screen and real input", False),
    (PY_LIVE_CMDS, "launches a live-screen command", True),
]

# ── Swift TYPE-POSITION exemption (class-level fix, 2026-08-25) ────────────
# ZoneTilerWM test-support files carry ~20 NSScreen markers that are all
# TYPE annotations on fakes' protocol surfaces (`[NSScreen]`, `NSScreen?`,
# `-> NSScreen`, `is/as? NSScreen`, `Foo<NSScreen>`) — none reads the live
# display. An OCCURRENCE sitting in a type shape annotates; only member
# access reaches the runtime object, so the guard below never exempts an
# occurrence followed by `.` (NSScreen.main, NSScreen.screens). The
# predicate is attached per-pattern (`True` third element in SWIFT_PATTERNS),
# so any other bare-identifier pattern can opt in without new special cases.

_SWIFT_CAST_BEFORE = re.compile(r"\b(?:is|as)[?!]?\s*(?:\[\s*)?$")
_SWIFT_RETURN_BEFORE = re.compile(r"->\s*(?:\[\s*)?$")
# `: T` · `: [T]` · `: [K: T]` — annotations, parameter types, dict types
_SWIFT_ANNOT_BEFORE = re.compile(r":\s*(?:\[\s*(?:\w+\s*:\s*)?\s*)?$")


def _generic_context(prefix):
    """True when `prefix` ends inside an unclosed `<...>` argument list."""
    depth = 0
    for ch in reversed(prefix):
        if ch == ">":
            depth += 1
        elif ch == "<":
            if depth == 0:
                return True
            depth -= 1
        elif ch in "([":
            return False  # entered a call/literal region first — not generic
    return False


def _in_swift_type_position(probe, match):
    """Is THIS occurrence of an identifier a Swift type, not a live use?"""
    if re.match(r"\s*\.", probe[match.end():]):
        return False  # member access — the identifier IS being used here
    prefix = probe[:match.start()]
    return bool(
        _SWIFT_CAST_BEFORE.search(prefix)
        or _SWIFT_RETURN_BEFORE.search(prefix)
        or _SWIFT_ANNOT_BEFORE.search(prefix)
        or _generic_context(prefix)
    )


LANGUAGES = {
    ".swift": (("#", "//", "///"),
               [(re.compile(p), w, False,
                 _in_swift_type_position if t else None)
                for p, w, *t in SWIFT_PATTERNS]),
    ".m": (("//",), [(re.compile(p), w, False, None) for p, w in OBJC_PATTERNS]),
    ".mm": (("//",), [(re.compile(p), w, False, None) for p, w in OBJC_PATTERNS]),
    ".py": (("#",), [(p, w, needs_exec, None)
                     for p, w, needs_exec in PYTHON_RULES]),
}


# Comments and string literals are PROSE. The absorbed API names all appear
# in test comments explaining why the screen is off limits, and a gate that
# fails on its own documentation gets turned off within the week (necrohand
# learned this the same week it shipped its checker). Masking replaces
# matched spans with same-length whitespace / empty strings so LINE NUMBERS
# are preserved for marker lookup on the original text.
SWIFT_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
SWIFT_LINE_COMMENT = re.compile(r"//[^\n]*")
SWIFT_STRING = re.compile(r'"(?:\\.|[^"\\])*"')


def _mask_string(match):
    """Empty a matched string literal WITHOUT changing the line count.

    A mask that deletes newlines (e.g. collapsing a triple-quoted docstring
    to '""') desynchronizes the masked line list from the original lines,
    and every marker lookup / reported line number after it indexes the
    WRONG line — or crashes outright (found 2026-08-25 on necrohand, whose
    tests carry multi-line docstrings). First line becomes '""'; every
    further line of the literal becomes an empty line.
    """
    s = match.group(0)
    if "\n" not in s:
        return '""'
    parts = s.split("\n")
    return "\n".join(['""'] + [""] * (len(parts) - 1))


def swift_code_only(text):
    """Swift source with comments blanked and strings emptied, per line.

    EVERY substitution here must be LINE-PRESERVING: callers index the
    result positionally against text.splitlines().
    """
    text = SWIFT_BLOCK_COMMENT.sub(
        lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    text = SWIFT_LINE_COMMENT.sub(lambda m: " " * len(m.group(0)), text)
    return SWIFT_STRING.sub(_mask_string, text)


def _has_marker(lines, idx):
    """`screen-ok:` on this line or the one above it."""
    for probe in (lines[idx], lines[idx - 1] if idx > 0 else ""):
        if ALLOW_MARKER in probe:
            return True
    return False


def check_text(rel, text):
    """Yield (line_no, why) for every unguarded presentation call."""
    suffix = os.path.splitext(rel)[1]
    if suffix not in LANGUAGES:
        return []
    comment_prefixes, patterns = LANGUAGES[suffix]
    if suffix == ".py" and PY_GUARD.search(text):
        return []  # declared its headless contract for child launches
    if suffix == ".swift":
        # Patterns match MASKED lines (comments/strings are prose); markers
        # are looked up on the ORIGINAL lines, which masking preserves 1:1.
        scan_lines = swift_code_only(text).splitlines()
    else:
        scan_lines = None
    lines = text.splitlines()
    found = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if any(stripped.startswith(pfx) for pfx in comment_prefixes):
            continue  # a comment explaining the rule is not a violation
        if _has_marker(lines, i):
            continue
        probe = scan_lines[i] if scan_lines is not None else line
        for pattern, why, needs_exec, exempt in patterns:
            hits = list(pattern.finditer(probe))
            if exempt is not None:
                # Occurrence-level: a line flags only when at least one of
                # its matches sits OUTSIDE the exempted shape (type position).
                hits = [m for m in hits if not exempt(probe, m)]
            if not hits:
                continue
            if needs_exec and not PY_EXECUTES.search(line):
                continue  # names a live command but executes nothing
            found.append((i + 1, why))
            break
    return found


def collect_targets(root, staged, paths, scope):
    """Repo-root-relative candidates: explicit args, else --scope matches."""
    if paths:
        out = []
        for p in paths:
            p = os.path.relpath(os.path.abspath(p), root)
            if os.path.isdir(os.path.join(root, p)):
                for dirpath, dirnames, filenames in os.walk(os.path.join(root, p)):
                    dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                    for name in sorted(filenames):
                        out.append(os.path.relpath(os.path.join(dirpath, name), root))
            else:
                out.append(p)
        return sorted(set(out)), None
    if scope:
        return None, [f for f in listed_files(root, staged=staged)
                      if fnmatch.fnmatch(f, scope)]
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", help="files/dirs to scan (test targets)")
    ap.add_argument("--scope", default=None,
                    help="glob over repo-root-relative tracked files")
    ap.add_argument("--staged", action="store_true")
    args = ap.parse_args()

    if not args.paths and not args.scope:
        ap.error("no targets: pass paths or --scope (e.g. --scope 'tests/*')")

    root = repo_root() or os.getcwd()
    explicit, scoped = collect_targets(root, args.staged, args.paths, args.scope)
    if explicit is not None:
        targets = explicit
    elif scoped:
        targets = sorted(scoped)
    else:
        print("✗ [no_screen] --scope matched no tracked files", file=sys.stderr)
        return 1

    bad = []
    checked = 0
    for rel in targets:
        if os.path.splitext(rel)[1] not in LANGUAGES:
            continue
        blob = content_bytes(root, rel, staged=args.staged)
        if blob is None or b"\0" in blob[:8000]:
            continue
        checked += 1
        for line_no, why in check_text(rel, blob.decode("utf-8", "replace")):
            bad.append((rel, line_no, why))

    if bad:
        print(
            f"✗ [no_screen] {len(bad)} presentation call(s) in test targets:",
            file=sys.stderr,
        )
        for path, line_no, why in bad:
            print(f"    {path}:{line_no}: {why}", file=sys.stderr)
        print(
            "\n  Render offscreen instead, or mark the line `screen-ok:` with\n"
            "  a reason. Runtime half: source lib/headless_env.sh and launch\n"
            "  GUI children under GOH_HEADLESS=1.",
            file=sys.stderr,
        )
        return 1

    scope = "staged" if args.staged else "scanned"
    print(f"→ [no_screen] OK — {checked} test target(s) {scope}, nothing renders to the display")
    return 0


if __name__ == "__main__":
    sys.exit(main())
