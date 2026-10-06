"""The self-proof for `checks/check_display_seam.py`, split out so neither file crowds the cap.

Run from `check_display_seam.py --probe`, which is what `check_probes_pass.py` discovers. Every
rule is driven red against a fixture, and the shapes are the ones that were WRONG somewhere: a
multi-line `subprocess.run([...])` (what `ruff format` emits), a live-tier helper NAMED in prose
rather than called, and a bare `screen-ok:` marker with no reason. A gate that cannot fail in the
shape it was written for is not a gate (SUPERSOTA R3).
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import check_display_seam  # noqa: E402  (the module under proof, driven not copied)
from _gitutil import foreign_repo_env  # noqa: E402

SEAM = "func present() { w.orderFrontRegardless() }\n"
# A file that is none of the gate's business, present in every fixture so that "clean" means
# "this gate examined something and found nothing", never "this gate examined nothing".
QUIET = "func configure() { let n = 3 }\n"
BASE = {
    "seam": ["Core/DisplayPolicy.swift"],
    "markers": ["screen-ok:", "input-ok:"],
    "shell_guard": "ZT_NO_SCREEN",
    "live_helper": "require_live_screen",
}

# Every alternative in the Swift vocabulary gets a case, because a pattern nobody drives is a
# pattern nobody knows works (SUPERSOTA R2: the table IS the specification). Dropping any one of
# them must turn exactly its own row red.
SWIFT_SHAPES = (
    ("orderFront", "w.orderFront(nil)"),
    ("orderFrontRegardless", "w.orderFrontRegardless()"),
    ("makeKeyAndOrderFront", "w.makeKeyAndOrderFront(nil)"),
    ("showWindow", "w.showWindow(nil)"),
    ("NSApp.activate", "NSApp.activate(ignoringOtherApps: true)"),
    ("setActivationPolicy", "NSApp.setActivationPolicy(.regular)"),
    ("NSApplication.shared.run", "NSApplication.shared.run()"),
    ("runModal", "w.runModal()"),
    ("a permission prompt", "AXIsProcessTrustedWithOptions(nil)"),
    ("a screen-capture grant", "CGRequestScreenCaptureAccess()"),
    ("opening a system pane", 'NSWorkspace.shared.open(URL(string: "x")!)'),
    ("reading the real pointer", "NSEvent.mouseLocation"),
    ("reading the button state", "NSEvent.pressedMouseButtons"),
    ("a global event tap", "NSEvent.addGlobalMonitorForEvents(matching: .mouseMoved) { _ in }"),
    ("a local event tap", "NSEvent.addLocalMonitorForEvents(matching: .keyDown) { _ in }"),
    ("CGEvent.tapCreate", "CGEvent.tapCreate(tap: .cgEventTap)"),
    ("CGEvent.post", "CGEvent.post(tap: .cghidEventTap)"),
    ("warping the cursor", "CGWarpMouseCursorPosition(p)"),
    ("moving the cursor", "CGDisplayMoveCursorToPoint(p)"),
    ("hiding the cursor", "NSCursor.hide()"),
    ("tap posting", "monitor.post(tap: .cghidEventTap)"),
)
SWIFT_CASES = tuple(
    (f"Swift: {label} is refused", {"Core/quiet.swift": QUIET, "UI/x.swift": src + "\n"}, {}, True)
    for label, src in SWIFT_SHAPES
) + (
    (
        "Swift: a clean call site is not a finding",
        {"Core/quiet.swift": QUIET, "UI/x.swift": "func f() { NSView(frame: .zero) }\n"},
        {},
        False,
    ),
    (
        "Swift: a string naming a shape is prose, not code",
        {
            "Core/quiet.swift": QUIET,
            "UI/x.swift": 'let s = "w.orderFrontRegardless() is forbidden"\n',
        },
        {},
        False,
    ),
)

# ...and the same for the live-command vocabulary, each driven through a real executor.
LIVE_SHAPES = (
    ("screencapture", '["screencapture", "-x", "a.png"]'),
    ("cliclick", '["cliclick", "c:1,2"]'),
    ("osascript", '["osascript", "-e", "1"]'),
    ("System Events", '["osascript", "-e", "tell application "System Events""]'),
    ("tell application", '["osascript", "-e", "tell application "Finder""]'),
    ("to activate", '["osascript", "-e", "tell app "X" to activate"]'),
)
APP = r"bin/ZeroThunder.app/Contents/MacOS/ZeroThunder"
LIVE_CASES = tuple(
    (
        f"Python: an undeclared harness executing {label} is refused",
        {"Core/quiet.swift": QUIET, "tests/x.py": f"import subprocess\nsubprocess.run({args})\n"},
        {},
        True,
    )
    for label, args in LIVE_SHAPES
) + (
    (
        "Python: naming a live command in prose executes nothing",
        {
            "Core/quiet.swift": QUIET,
            "tests/x.py": '"""screencapture is unreliable in CI."""\nprint(1)\n',
        },
        {},
        False,
    ),
    (
        "Python: os.system executes as well as subprocess",
        {"Core/quiet.swift": QUIET, "tests/x.py": 'import os\nos.system("screencapture a.png")\n'},
        {},
        True,
    ),
    (
        "Python: check_output executes too",
        {
            "Core/quiet.swift": QUIET,
            "tests/x.py": 'from subprocess import check_output\ncheck_output(["screencapture", "a.png"])\n',
        },
        {},
        True,
    ),
    (
        "Python: the APP BINARY counts as live, with no offscreen flag",
        {
            "Core/quiet.swift": QUIET,
            "tests/x.py": f'import subprocess\nsubprocess.run(["{APP}"])\n',
        },
        {"app_launch": APP.replace("/", "\\/").replace(".", "\\.")},
        True,
    ),
    (
        "Python: the SAME launch is fine once an offscreen flag is declared",
        {
            "Core/quiet.swift": QUIET,
            "tests/x.py": f'import subprocess\nsubprocess.run(["{APP}", "--golden-out"])\n',
        },
        {
            "app_launch": APP.replace("/", "\\/").replace(".", "\\."),
            "offscreen_flags": ["--golden-out"],
        },
        False,
    ),
    (
        "Python: a SECOND offscreen mode inherits none of the first's recognition",
        {
            "Core/quiet.swift": QUIET,
            "tests/x.py": f'import subprocess\nsubprocess.run(["{APP}", "--offscreen-live"])\n',
        },
        {
            "app_launch": APP.replace("/", "\\/").replace(".", "\\."),
            "offscreen_flags": ["--golden-out"],
        },
        True,
    ),
)

# (label, files, policy overrides, WANTED EXIT CODE). 1 = the rule fired, 0 = it did not,
# 2 = a config error. Stating the code rather than a boolean is what lets the refusal cases
# live here instead of in a second table with its own convention.
CASES = (
    SWIFT_CASES
    + LIVE_CASES
    + (
        (
            "the seam itself is exempt",
            {"Core/DisplayPolicy.swift": SEAM, "Core/quiet.swift": QUIET},
            {},
            0,
        ),
        (
            "a direct orderFrontRegardless() outside the seam is a violation",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "UI/win.swift": "func f() { w.orderFrontRegardless() }\n",
            },
            {},
            1,
        ),
        (
            "a JUSTIFIED screen-ok marker is honoured",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "UI/win.swift": "// screen-ok: attended debug program\nfunc f() { w.orderFrontRegardless() }\n",
            },
            {},
            0,
        ),
        (
            "a BARE screen-ok marker silences nothing",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "UI/win.swift": "// screen-ok:\nfunc f() { w.orderFrontRegardless() }\n",
            },
            {},
            1,
        ),
        (
            "an event TAP is the same problem as a window",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "Scene/tap.swift": "func f() { CGEvent.tapCreate(tap: .cgEventTap) }\n",
            },
            {},
            1,
        ),
        (
            "input-ok is honoured where screen-ok is",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "Scene/tap.swift": "// input-ok: measured live taps during a run\nfunc f() { NSEvent.mouseLocation }\n",
            },
            {},
            0,
        ),
        (
            "an undeclared screencapture harness is a violation",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "tests/cap.py": 'import subprocess\nsubprocess.run(["screencapture", "-x", "a.png"])\n',
            },
            {},
            1,
        ),
        (
            # The shape `ruff format` emits, and the shape the consumer's own harnesses are written
            # as: the executor sits on an EARLIER line than the command.
            "a MULTI-LINE subprocess call is caught",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "tests/multi.py": 'import subprocess\ndef grab(p):\n    subprocess.run(\n        ["screencapture", "-x", p],\n        check=False,\n    )\n',
            },
            {},
            1,
        ),
        (
            "NAMING the helper in prose buys no exemption",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "tests/prose.py": '"""Callers declare the live tier with require_live_screen, so fine."""\nimport subprocess\nsubprocess.run(["screencapture", "-x", "a.png"])\n',
            },
            {},
            1,
        ),
        (
            "a declared live-tier harness with a real reason is accepted",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "tests/live.py": 'from p import require_live_screen\nrequire_live_screen("x.py", "drives the real compositor via SkyLight")\nimport subprocess\nsubprocess.run(["screencapture", "-x", "a.png"])\n',
            },
            {"scan": ["tests"]},
            0,
        ),
        (
            "a live declaration with NO reason is a violation",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "tests/live.py": 'from p import require_live_screen\nrequire_live_screen("x.py")\nimport subprocess\nsubprocess.run(["screencapture", "-x", "a.png"])\n',
            },
            {},
            1,
        ),
        (
            "a PLACEHOLDER live reason is a violation",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "tests/live.py": 'from p import require_live_screen\nrequire_live_screen("x.py", "needs the real screen")\n',
            },
            {},
            1,
        ),
        (
            "an unscanned root is not scanned",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "Core/quiet.swift": QUIET,
                "tools/lab/win.swift": SEAM,
            },
            {"scan": ["Core"]},
            0,
        ),
        (
            "the shell pass is NOT scoped: a live script is a screen problem wherever it lives",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "Core/quiet.swift": QUIET,
                "tools/lab/shot.sh": "#!/bin/bash\nscreencapture -x a.png\n",
            },
            {"scan": ["Core"]},
            1,
        ),
        (
            "an unguarded shell harness is a violation, wherever it lives",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "build/shot.sh": "#!/bin/bash\nscreencapture -x a.png\n",
            },
            {},
            1,
        ),
        (
            "a shell harness carrying the offscreen guard is accepted",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "build/shot.sh": '#!/bin/bash\n[ -n "${ZT_NO_SCREEN:-}" ] && exit 3\nscreencapture -x a.png\n',
            },
            {},
            0,
        ),
        (
            "an excluded file is exempt",
            {
                "Core/DisplayPolicy.swift": SEAM,
                "Core/quiet.swift": QUIET,
                "UI/win.swift": "func f() { w.orderFront(nil) }\n",
            },
            {"exclude": ["UI/win.swift"], "exclude_why": {"UI/win.swift": "attended helper"}},
            0,
        ),
        (
            "merely DOCUMENTING the helper is not a declaration",
            {
                "Core/quiet.swift": QUIET,
                "tests/x.py": '"""Callers use require_live_screen to declare a harness."""\nprint(1)\n',
            },
            {},
            0,
        ),
        (
            "a policy whose scan roots match no source is REFUSED, not passed",
            {"Core/quiet.swift": QUIET},
            {"scan": ["nowhere"]},
            2,
        ),
    )
)

# The config half. A policy that cannot be honoured is exit 2, never a pass — including a key
# this checker does not know, which would otherwise read as "that rule is off".
CONFIG_CASES = (
    # `scean` is a plausible typo of `scan`. If an unknown key were ignored rather than refused,
    # the fixture would fall back to BASE's roots and find clean source -- which is exactly the
    # silent disabling this case exists to catch, so the fixture carries real files on purpose.
    ("an unknown policy key is refused", {"scean": ["Core"]}),
    ("a seam that does not exist is refused", {"seam": ["Core/Nope.swift"]}),
    ("a policy that is not a JSON object is refused", {"scan": "Core"}),
    ("an exclusion with no reason is refused", {"exclude": ["Core/quiet.swift"]}),
)


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", root, *args],
        capture_output=True,
        text=True,
        check=False,
        env=foreign_repo_env(),
    ).returncode


def _build(root, files, policy, template):
    # The seam file is present in every fixture unless a case says otherwise, because the policy
    # names it and a policy naming a seam that does not exist is exit 2 — which would mask the
    # rule the case is about.
    files = {"Core/DisplayPolicy.swift": SEAM, **files}
    for rel, body in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
    merged = dict(BASE)
    merged.update(policy)
    with open(os.path.join(root, "policy.json"), "w", encoding="utf-8") as handle:
        json.dump(merged, handle)
    # A COPY of one freshly initialised `.git`, and nothing else. The probe drives FULL mode, which
    # lists the worktree (`ls-files --cached --others`), so an index is not what is under test, and
    # no case commits, so no identity is read. Measured 2026-10-05: the git spawns were the whole
    # cost of this probe -- 4.2 s, run once per probe and 23 more times by the rule-drop
    # calibration -- and `init` + `config` x2 + `add` per fixture were four of them. A fresh case
    # directory per case is unchanged; only the empty repository it starts from is shared.
    if template is not None:
        shutil.copytree(template, os.path.join(root, ".git"))


def _listing(root, staged=False):
    """Every file a case wrote, as `ls-files --cached --others` lists a fixture with no ignores."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        out.extend(os.path.relpath(os.path.join(dirpath, f), root) for f in filenames)
    return sorted(out)


@contextlib.contextmanager
def _listed_directly():
    """The gate's rules, without git under every case. Each case cost two git spawns (`rev-parse`,
    `ls-files`) and a `.git` copy -- 31 cases, and the rule-drop calibration runs the whole probe 24
    more times: 44 s of the suite, nearly all of it sys time, the class that serializes across
    sessions (tools/session_bench.py, 2026-10-06). The listing is `_gitutil`'s, proven by its own
    tests; ONE case per probe still drives it through real git, so a probe cannot pass over a
    listing that lost the files."""
    saved = check_display_seam.repo_root, check_display_seam.listed_files
    check_display_seam.repo_root = lambda: None  # main() falls back to the working directory
    check_display_seam.listed_files = _listing
    try:
        yield
    finally:
        check_display_seam.repo_root, check_display_seam.listed_files = saved


def _drive(root, argv):
    """main() in a FIXTURE repo, with its own reporting swallowed.

    os.chdir is the only way in: the checker resolves its root the way every checker does, from
    the working directory. The output is swallowed because these are 19 fixture repos and their
    findings would bury the case verdicts; the code is what is under test, not the prose.
    """
    saved = os.getcwd()
    sink = io.StringIO()
    try:
        os.chdir(root)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            return check_display_seam.main(argv)
    finally:
        os.chdir(saved)


def _template(td):
    """One empty repository's `.git`, made by git itself, for `_build` to copy per case."""
    seed = os.path.join(td, "template")
    os.makedirs(seed)
    _git(seed, "init", "-q", "--template=")
    return os.path.join(seed, ".git")


def probe():
    import tempfile

    from tui.lib import err, ok

    bad = 0
    with tempfile.TemporaryDirectory() as td:
        template = _template(td)
        for n, (label, files, policy, want) in enumerate(CASES):
            # A fresh directory per case. Reusing one made a case see the PREVIOUS case's files,
            # which is a fixture that cannot disagree with anything -- the exact shape R3 names.
            root = os.path.join(td, f"case{n:02d}")
            through_git = n == 0  # the one case that proves the real listing (_listed_directly)
            _build(root, files, policy, template if through_git else None)
            with contextlib.nullcontext() if through_git else _listed_directly():
                code = _drive(root, ["--policy", "policy.json"])
            if code == want:
                ok(f"probe: {label}")
            else:
                err(f"probe: {label} — wanted exit {want}, got {code}")
                bad += 1
        for n, (label, policy) in enumerate(CONFIG_CASES):
            root = os.path.join(td, f"cfg{n:02d}")
            # TWO clean files, so that with a config rule dropped the run still has something to
            # examine. One file, when that file is the excluded one, made the zero-scan refusal
            # produce the same exit 2 the case was asserting -- two rules, one verdict.
            _build(root, {"Core/quiet.swift": QUIET, "UI/win.swift": QUIET}, policy, None)
            with _listed_directly():
                refused = _drive(root, ["--policy", "policy.json"]) == 2
            if refused:
                ok(f"probe: {label}")
            else:
                err(f"probe: {label} — the config error was not refused")
                bad += 1
    if bad:
        err(f"display-seam probe: {bad} case(s) wrong — the gate does not measure its rule")
        return 1
    ok(
        f"display-seam probe: {len(CASES)} shapes and {len(CONFIG_CASES)} config errors behave "
        f"as declared"
    )
    return 0


if __name__ == "__main__":
    sys.exit(probe())
