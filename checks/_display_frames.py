"""The display seam's third rule: a render that counts frames must be able to deliver them.

Measured in `games/ZeroThunder` (2026-10-08): fourteen harnesses passed `--golden-frames 90`, and
the golden loop drove those frames through the app's ON-SCREEN `MTKView`, left display-linked. With
the built-in panel on, the view dropped every other draw -- 45 frames of 2 ticks -- and the result
tracked the WindowServer's display state at 7 of 7 observations. Nothing compared frames drawn
to frames requested, and the bistability harness was blind by its own docstring: it catches a
latch, and a display-paced render is a SYSTEMATIC half rate, deterministic within one display
state. Pinning the view paused was measured too, and delivered 45 of 90 as well.

Two halves, because the class has two sites:

  SWIFT   An `MTKView` drawn by hand (`view.draw()`) is an offscreen render paced by an on-screen
          view: the draw is whatever the display link lets through. It routes through the seam or
          carries a justified marker, like every other presentation call. The receivers are found
          TREE-WIDE (a `var`/`let` declared or built as an `MTKView` anywhere in the scan), because
          the draw is usually `app.metalView?.draw()` in a file that never names the type.

  PYTHON  A harness that requests frames from the app (a string literal matching the policy's
          `frames_flag`, `--golden-frames` by default) must CALL the policy's `frames_helper` with
          the count it requested and the count the renderer reports it drew. A second argument that
          is a literal, or the same expression as the first, reads back a constant -- that is not a
          measurement, and it is refused as one.

Split from `check_display_seam.py` so neither file crowds the 500-line cap; the gate imports the
rule rather than restating it, and the probe imports `FRAMES_CASES` from here.
"""

from __future__ import annotations

import ast
import re

from _swift_text import swift_code_only

# The flag a harness hands the APP. A bare `--frames` was the first default and was wrong on the
# estate's one consumer: three scripts declare a `--frames` of their own (an argparse option, or a
# harness's flag driven by its own test), which asks no renderer for anything.
DEFAULT_FRAMES_FLAG = r"--golden-frames"
# A script DECLARING its own option of that name is not asking the app for frames.
DECLARES = ("add_argument",)
DEFAULT_FRAMES_HELPER = "require_frames_drawn"

# `var metalView: MTKView?`, `let view = MTKView(frame: f, device: d)`, `private var v: MTKView!`.
VIEW_DECL = re.compile(r"\b(?:var|let)\s+(\w+)\s*(?::\s*MTKView\b|=\s*MTKView\s*\()")
# A draw with NO arguments: `NSView.draw(_:)` takes a rect, so this is the MTKView's own entry.
DRAW_CALL = r"\b(?:{names})\s*[?!]?\s*\.\s*draw\s*\(\s*\)"


def onscreen_views(swift_texts):
    """Every name declared or built as an `MTKView` across the scanned Swift source."""
    names = set()
    for text in swift_texts:
        names.update(VIEW_DECL.findall(swift_code_only(text)))
    return names


def check_swift_frames(rel, text, views, silenced, out):
    """`views` comes from `onscreen_views` over the whole scan; `silenced(lines, i)` is the
    policy's justified-marker test, shared with the presentation rule."""
    if not views:
        return
    draw = re.compile(DRAW_CALL.format(names="|".join(sorted(map(re.escape, views)))))
    lines = text.split("\n")
    for i, line in enumerate(swift_code_only(text).split("\n")):
        if draw.search(line) and not silenced(lines, i):
            out.append(
                (
                    rel,
                    i + 1,
                    "draws an on-screen MTKView by hand: its frames are paced by the display, "
                    "so render offscreen through the seam",
                )
            )
            return


def _callee(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def check_python_frames(rel, text, flag, helper, out):
    """`flag` is a compiled pattern matched against WHOLE string literals (`--x` or `--x=N`), so a
    sentence that mentions the flag, a docstring or a help text, requests nothing."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return  # not this rule's to judge: the file does not run, so it requests nothing
    declared = {
        id(arg)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _callee(node) in DECLARES
        for arg in node.args
    }
    request = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in declared
            and flag.fullmatch(node.value.split("=", 1)[0])
        ),
        None,
    )
    if request is None:
        return
    calls = [
        node for node in ast.walk(tree) if isinstance(node, ast.Call) and _callee(node) == helper
    ]
    if not calls:
        out.append(
            (
                rel,
                request.lineno,
                f"requests frames ({request.value}) and never reads the count back: "
                f"call {helper}(requested, drawn)",
            )
        )
        return
    for call in calls:
        args = call.args
        if (
            len(args) < 2
            or isinstance(args[1], ast.Constant)
            or ast.dump(args[0]) == ast.dump(args[1])
        ):
            out.append(
                (
                    rel,
                    call.lineno,
                    f"{helper}() reads back a constant: the drawn count must come from the "
                    f"renderer that drew",
                )
            )
            return


HARNESS = (
    'import subprocess\nsubprocess.run(["app", "--golden-out", "a.png", "--golden-frames", "90"])\n'
)
QUIET = "func configure() { let n = 3 }\n"
VIEW = "final class A { var metalView: MTKView? }\n"
# (label, files, policy overrides, WANTED EXIT CODE), the probe's own convention.
FRAMES_CASES = (
    (
        "frames: a harness that requests frames and reads none back is refused",
        {"Core/quiet.swift": QUIET, "tests/g.py": HARNESS},
        {},
        1,
    ),
    (
        "frames: the flag's --x=N spelling is a request too",
        {"Core/quiet.swift": QUIET, "tests/g.py": 'run(["app", "--golden-frames=90"])\n'},
        {},
        1,
    ),
    (
        "frames: reading the renderer's count back is accepted",
        {
            "Core/quiet.swift": QUIET,
            "tests/g.py": HARNESS + "require_frames_drawn(90, report['frames_drawn'])\n",
        },
        {},
        0,
    ),
    (
        "frames: reading back a literal is refused",
        {"Core/quiet.swift": QUIET, "tests/g.py": HARNESS + "require_frames_drawn(frames, 90)\n"},
        {},
        1,
    ),
    (
        "frames: reading back the request itself is refused",
        {"Core/quiet.swift": QUIET, "tests/g.py": HARNESS + "require_frames_drawn(n, n)\n"},
        {},
        1,
    ),
    (
        "frames: a docstring naming the flag requests nothing",
        {"Core/quiet.swift": QUIET, "tests/g.py": '"""Pass --golden-frames to the app."""\n'},
        {},
        0,
    ),
    (
        "frames: a script declaring its own option of that name requests nothing",
        {"Core/quiet.swift": QUIET, "tests/g.py": 'ap.add_argument("--golden-frames", type=int)\n'},
        {},
        0,
    ),
    (
        "frames: the policy names the flag",
        {"Core/quiet.swift": QUIET, "tests/g.py": 'run(["app", "--ticks", "9"])\n'},
        {"frames_flag": "--ticks"},
        1,
    ),
    (
        "frames: an MTKView drawn by hand in another file is refused",
        {"Core/quiet.swift": VIEW, "Core/step.swift": "func s() { app.metalView?.draw() }\n"},
        {},
        1,
    ),
    (
        "frames: a view built in place and drawn is refused",
        {"Core/quiet.swift": QUIET, "UI/c.swift": "let view = MTKView(frame: f)\nview.draw()\n"},
        {},
        1,
    ),
    (
        "frames: drawing a view that is not an MTKView is not this rule's",
        {"Core/quiet.swift": VIEW, "UI/c.swift": "func s() { layer.draw() }\n"},
        {},
        0,
    ),
    (
        "frames: a justified marker silences a hand draw",
        {
            "Core/quiet.swift": VIEW,
            "Core/step.swift": "// screen-ok: attended step debugger, a human watches it\n"
            "func s() { app.metalView?.draw() }\n",
        },
        {},
        0,
    ),
)
