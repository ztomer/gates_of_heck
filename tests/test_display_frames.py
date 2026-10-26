"""`checks/_display_frames.py` — the display seam's third rule, and its own calibration.

A harness that requests N frames must read N back from the renderer, and an on-screen `MTKView`
drawn by hand is an offscreen render paced by the display (ZeroThunder, 2026-10-08: 45 of 90
frames with the built-in panel on, and nothing compared the two). The rows live in
`FRAMES_CASES` and run inside the seam gate's own `--probe`; these tests drop one rule at a time
and require that probe to go red, and pin the count rule on a fixture rendering at half rate.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from conftest import REPO_ROOT as ROOT

CHECKS = ROOT / "checks"
FRAMES = CHECKS / "_display_frames.py"
sys.path.insert(0, str(CHECKS))

import _display_frames


def findings_for_python(text, flag=_display_frames.DEFAULT_FRAMES_FLAG):
    import re

    out = []
    helper = _display_frames.DEFAULT_FRAMES_HELPER
    _display_frames.check_python_frames("h.py", text, re.compile(flag), helper, out)
    return out


HALF_RATE = '''import subprocess


def render(frames):
    """A renderer paced by the display: it draws every other frame it is asked for."""
    drawn = frames // 2
    subprocess.run(["app", "--golden-frames", str(frames)], check=False)
    return drawn


requested = 90
require_frames_drawn(requested, render(requested))
'''


def test_a_half_rate_render_is_caught_on_the_count_not_on_a_pixel():
    """The red-first case 4.6 names. The static rule accepts this harness, because it reads the
    renderer's own count; the helper's contract is what turns a half-rate run red at runtime."""
    assert findings_for_python(HALF_RATE) == []
    namespace = {}

    def require_frames_drawn(requested, drawn):
        if drawn != requested:
            raise SystemExit(2)

    namespace["require_frames_drawn"] = require_frames_drawn
    namespace["subprocess"] = type("S", (), {"run": staticmethod(lambda *a, **k: None)})
    body = HALF_RATE.replace("import subprocess\n", "")
    with pytest.raises(SystemExit) as stop:
        exec(compile(body, "half_rate.py", "exec"), namespace)  # noqa: S102 -- the fixture under test
    assert stop.value.code == 2


def test_a_harness_reading_back_its_own_request_is_refused():
    got = findings_for_python(HALF_RATE.replace("render(requested))", "requested)"))
    assert got and "constant" in got[0][2]


def test_a_harness_with_no_read_back_names_the_line_of_its_request():
    got = findings_for_python(
        'import subprocess\nx = 1\nsubprocess.run(["app", "--golden-frames", "9"])\n'
    )
    assert got == [
        (
            "h.py",
            3,
            (
                "requests frames (--golden-frames) and never reads the count back: "
                "call require_frames_drawn(requested, drawn)"
            ),
        )
    ]


def test_source_that_does_not_parse_is_not_this_rules_to_judge():
    assert findings_for_python('run(["--golden-frames"]\n') == []


def test_views_are_found_by_declaration_and_by_construction():
    views = _display_frames.onscreen_views(
        [
            "var metalView: MTKView?\nprivate let other: MTKView!\n",
            "let built = MTKView(frame: f)\n// var commented: MTKView\n",
        ]
    )
    assert views == {"metalView", "other", "built"}


def test_a_policy_flag_that_is_not_a_pattern_is_refused(tmp_path):
    import check_display_seam

    with pytest.raises(ValueError, match="frames_flag"):
        check_display_seam.Policy({"frames_flag": "--(golden"}, str(tmp_path))


# ── the calibration: drop one rule, watch the seam probe's frames rows go red ───

RULES = (
    ("python-requests", 'flag.fullmatch(node.value.split("=", 1)[0])', "False"),
    ("python-split-equals", 'node.value.split("=", 1)[0]', "node.value"),
    ("python-whole-literal", "flag.fullmatch(", "flag.search("),
    ("python-declarations", "and id(node) not in declared", ""),
    ("python-helper-called", "    if not calls:\n", "    if False:\n"),
    ("python-constant", "or isinstance(args[1], ast.Constant)", ""),
    ("python-same-expression", "or ast.dump(args[0]) == ast.dump(args[1])", ""),
    ("swift-views", "names.update(VIEW_DECL.findall(swift_code_only(text)))", "pass"),
    ("swift-built-in-place", r"|=\s*MTKView\s*\()", ")"),
    ("swift-marker", "and not silenced(lines, i)", ""),
)


@pytest.mark.parametrize(("label", "old", "new"), RULES, ids=[r[0] for r in RULES])
def test_the_probe_goes_red_when_one_frames_rule_is_dropped(tmp_path, label, old, new):
    source = FRAMES.read_text(encoding="utf-8")
    assert old in source, f"the rule this case drops is not in the module: {old!r}"
    work = tmp_path / "checks"
    work.mkdir()
    (work / "_display_frames.py").write_text(source.replace(old, new, 1), encoding="utf-8")
    for name in (
        "check_display_seam.py",
        "_display_seam_probe.py",
        "_marker_reason.py",
        "_gitutil.py",
        "_swift_text.py",
        "_display_seam_tiers.py",
    ):
        (work / name).write_text((CHECKS / name).read_text(encoding="utf-8"), "utf-8")
    got = subprocess.run(
        [sys.executable, str(work / "check_display_seam.py"), "--probe"],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(ROOT)),
        stdin=subprocess.DEVNULL,
    )
    assert got.returncode == 1, f"the probe stayed green without the {label} rule:\n{got.stdout}"
    assert "frames:" in got.stderr, got.stderr
