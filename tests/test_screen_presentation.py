"""check_no_screen_presentation.py + lib/headless_env.sh — contract tests.

The static half is proven against fixture trees under
tests/fixtures/screen_presentation/ (copied into throwaway git repos so the
--staged/index path is exercised too). The runtime half is exercised with a
real bash that sources the real lib.
"""

import shutil
import subprocess

from conftest import REPO_ROOT, commit_all, run_check, write

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "screen_presentation"
LIB = REPO_ROOT / "lib" / "headless_env.sh"


def seed(repo, monkeypatch_dir=None):
    """Copy the screen-presentation fixtures into `repo` and commit."""
    dest = repo / "tests_src"
    shutil.copytree(FIXTURES, dest)
    commit_all(repo)
    return dest


# ---- static half: violations -------------------------------------------------


# ---- necrohand differential: 13 synthetic screen violations ------------------
#
# Reconstructed from ~/Projects/necrohand tools/check_headless_tests.py
# (their static checker caught 13/13; this one caught 3/13 pre-absorption).
# Each file below is ONE violation; BEFORE the pattern absorption only the
# orderFront / NSScreen / CGDisplay files were flagged.

VIOLATION_FIXTURES = [
    "Bad.swift",                    # orderFrontRegardless        (caught before)
    "bad_nsscreen.swift",           # NSScreen.main               (caught before)
    "bad_cgdisplay.swift",          # CGDisplayBounds             (caught before)
    "bad_custom_window.swift",      # NecrohandWindow(
    "bad_overlay_view.swift",       # NecrohandOverlayView(
    "bad_presenting_binding.swift", # render: .presenting
    "bad_screencapturekit.swift",   # SCShareableContent
    "bad_cgwindowlist.swift",       # CGWindowListCreateImage
    "bad_cgwindowlistcopyinfo.swift",
    "bad_cametallayer.swift",       # CAMetalLayer(
    "bad_nextdrawable.swift",       # nextDrawable(
    "bad_cgwarp_mouse.swift",       # CGWarpMouseCursorPosition
    "bad_nscursor.swift",           # NSCursor
    "bad_nsapplication_shared.swift",  # NSApplication.shared
]
# (14 entries: CGWindowListCopyWindowInfo is its own fixture so a regression
# in either CGWindowList* spelling is visible; the necrohand count of 13
# categories maps onto these plus NSApp, which shares a file with .shared.)

CLEAN_SWIFTUI_FIXTURE = "CleanSwiftUITests.swift"

# Swift TYPE positions (annotations, generics, casts, return/param types) name
# NSScreen without touching the display; USE positions read it live. The
# checker must separate the two.
TYPE_POSITION_FIXTURE = "type_positions_clean.swift"
USE_POSITION_FIXTURE = "bad_nsscreen_use.swift"


def test_swift_type_positions_are_not_flagged(repo):
    src = seed(repo)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py",
        str(src / TYPE_POSITION_FIXTURE),
    )
    assert r.returncode == 0, (
        f"type-position occurrences flagged:\n{r.stderr}"
    )


def test_swift_use_positions_still_flagged(repo):
    src = seed(repo)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py",
        str(src / USE_POSITION_FIXTURE),
    )
    assert r.returncode == 1, "use-position NSScreen reads were NOT flagged"
    assert "reads the real display's geometry" in r.stderr


def test_type_position_fixture_not_named_when_scanning_tree(repo):
    src = seed(repo)
    r = run_check(repo, "checks/check_no_screen_presentation.py", str(src))
    assert r.returncode == 1  # real violations remain in the tree
    assert TYPE_POSITION_FIXTURE not in r.stderr


def test_differential_all_violation_fixtures_caught(repo):
    src = seed(repo)
    missed = []
    for name in VIOLATION_FIXTURES:
        r = run_check(repo, "checks/check_no_screen_presentation.py",
                      str(src / name))
        if r.returncode != 1:
            missed.append(name)
    assert missed == [], (
        f"{len(missed)}/{len(VIOLATION_FIXTURES)} violation fixtures NOT "
        f"caught: {missed}"
    )


def test_clean_swiftui_test_code_stays_green(repo):
    src = seed(repo)
    r = run_check(repo, "checks/check_no_screen_presentation.py",
                  str(src / CLEAN_SWIFTUI_FIXTURE))
    assert r.returncode == 0, r.stderr


def test_prose_and_strings_are_masked_not_flagged(repo):
    src = seed(repo)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", str(src),
    )
    assert r.returncode == 1  # the tree still holds real violations
    assert CLEAN_SWIFTUI_FIXTURE not in r.stderr, (
        "clean SwiftUI fixture with prose/string mentions was flagged"
    )


def test_swift_orderfront_fails(repo):
    src = seed(repo)
    r = run_check(repo, "checks/check_no_screen_presentation.py", str(src))
    assert r.returncode == 1
    assert f"{src.name}/Bad.swift:" in r.stderr
    assert r.stdout == "" or "OK" not in r.stdout


def test_objc_make_key_and_order_front_fails(repo):
    src = seed(repo)
    r = run_check(repo, "checks/check_no_screen_presentation.py", str(src))
    assert r.returncode == 1
    assert "bad.m:2" in r.stderr


def test_pyautogui_fails_without_any_subprocess(repo):
    src = seed(repo)
    r = run_check(repo, "checks/check_no_screen_presentation.py", str(src))
    assert r.returncode == 1
    assert "pyautogui" in r.stderr


def test_py_live_command_via_subprocess_fails(repo):
    src = seed(repo)
    r = run_check(repo, "checks/check_no_screen_presentation.py", str(src))
    assert r.returncode == 1
    assert "bad_subprocess.py:3" in r.stderr


# ---- static half: exemptions -------------------------------------------------


def test_screen_ok_marker_exempts(repo):
    src = seed(repo)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", str(src / "Marked.swift")
    )
    assert r.returncode == 0, r.stderr


def test_py_marker_exempts(repo):
    src = seed(repo)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", str(src / "marked_live.py")
    )
    assert r.returncode == 0, r.stderr


def test_py_headless_guard_exempts_file(repo):
    src = seed(repo)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", str(src / "guarded.py")
    )
    assert r.returncode == 0, r.stderr


def test_prose_mention_is_not_a_violation(repo):
    # calibrate-the-instrument: a gate that flags its own documentation stops
    # being read. Prose + no execution must stay green.
    src = seed(repo)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", str(src / "prose_only.py")
    )
    assert r.returncode == 0, r.stderr


def test_clean_repo_passes(repo):
    write(repo, "tests/test_math.py", "def test_add():\n    assert 1 + 1 == 2\n")
    write(repo, "src/app.swift", "let x = 1\n")  # non-test target scanned by scope only
    commit_all(repo)
    r = run_check(repo, "checks/check_no_screen_presentation.py", "--scope", "tests/*")
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_scope_mode_finds_violation(repo):
    write(repo, "tests/Bad.swift", "window.orderFrontRegardless()\n")
    commit_all(repo)
    r = run_check(repo, "checks/check_no_screen_presentation.py", "--scope", "tests/*")
    assert r.returncode == 1
    assert "tests/Bad.swift:1" in r.stderr


def test_staged_scope_uses_index_content(repo):
    p = write(repo, "tests/Clean.swift", "let x = 1\n")
    stage_it = subprocess.run(["git", "-C", str(repo), "add", "tests/Clean.swift"])
    assert stage_it.returncode == 0
    # index holds clean content; dirtying the worktree must not matter
    p.write_text("window.makeKeyAndOrderFront(nil)\n", encoding="utf-8")
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", "--staged", "--scope", "tests/*"
    )
    assert r.returncode == 0, r.stderr
    # ...and staging the violation DOES fail via the same scope
    subprocess.run(["git", "-C", str(repo), "add", "tests/Clean.swift"], check=True)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", "--staged", "--scope", "tests/*"
    )
    assert r.returncode == 1
    assert "tests/Clean.swift:1" in r.stderr


def test_multi_line_string_does_not_desync_mask(repo):
    # Regression (2026-08-25, necrohand): a triple-quoted docstring spanning
    # lines used to be collapsed by the string mask, shrinking the masked
    # line list vs the source and crashing with IndexError. The mask is
    # LINE-PRESERVING, so prose inside multi-line strings stays green.
    src = "\n".join([
        "import XCTest",
        "",
        "final class DocstringTests: XCTestCase {",
        "    /// A docstring that mentions NSScreen and",
        "    /// CGDisplayBounds across several lines of prose.",
        "    func testNothingTouchesTheScreen() {",
        '        let doc = """',
        "        Prose mentioning orderFrontRegardless and NSCursor",
        "        inside a multi-line string literal.",
        '        """',
        "        XCTAssertEqual(doc.isEmpty, false)",
        "    }",
        "}",
    ])
    write(repo, "tests/DocstringTests.swift", src)
    commit_all(repo)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", "tests/DocstringTests.swift"
    )
    assert r.returncode == 0, (r.stdout, r.stderr)


# ---- single-pass state scanner (2026-08-25) ----------------------------------
#
# The sequential mask (block comments, then line comments, then strings) let a
# comment OPENER inside a string literal hijack the scan: "//x" inside a URL
# blanked the rest of its line, "/*" inside a literal opened a fake block span
# that hid real violations on later lines.


def test_line_comment_marker_inside_string_does_not_blank_line_tail(repo):
    # The // inside the URL used to start a fake line comment that blanked
    # everything after it ON THE SAME LINE — hiding the live NSScreen read.
    src = "\n".join([
        "import XCTest",
        'let url = "https://example.com"; _ = NSScreen.main',
    ])
    write(repo, "tests/UrlInString.swift", src)
    commit_all(repo)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", "tests/UrlInString.swift"
    )
    assert r.returncode == 1, "violation after an in-string // was NOT flagged"
    assert "reads the real display's geometry" in r.stderr


def test_block_comment_opener_inside_string_does_not_fake_span(repo):
    # "/* inside the literal used to open a fake block span reaching the next
    # real */, masking every line between them — including violations.
    src = "\n".join([
        "import XCTest",
        'let s = "value /* not a comment"',
        "_ = NSScreen.main",
        "/* real tail comment */",
    ])
    write(repo, "tests/FakeSpanTests.swift", src)
    commit_all(repo)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", "tests/FakeSpanTests.swift"
    )
    assert r.returncode == 1, "violation under a fake block span was NOT flagged"
    assert "reads the real display's geometry" in r.stderr


def test_escaped_quote_stays_in_string(repo):
    # \" must not terminate the literal: the code after it is still masked.
    src = "\n".join([
        'let s = "she said \\"orderFrontRegardless\\" aloud"',
        "let x = 1",
    ])
    write(repo, "tests/EscapedQuoteTests.swift", src)
    commit_all(repo)
    r = run_check(
        repo,
        "checks/check_no_screen_presentation.py",
        "tests/EscapedQuoteTests.swift",
    )
    assert r.returncode == 0, r.stderr


def test_quotes_inside_block_comment_are_not_code(repo):
    # Control case, verified working today and pinned here: a double quote
    # inside a block comment must NOT open a string state that leaks.
    src = "\n".join([
        "import XCTest",
        '/* a block comment quoting "NSScreen.main" in prose */',
        "func ok() { XCTAssertTrue(true) }",
    ])
    write(repo, "tests/QuotedCommentTests.swift", src)
    commit_all(repo)
    r = run_check(
        repo,
        "checks/check_no_screen_presentation.py",
        "tests/QuotedCommentTests.swift",
    )
    assert r.returncode == 0, r.stderr


def test_no_targets_is_usage_error(repo):
    r = run_check(repo, "checks/check_no_screen_presentation.py")
    assert r.returncode == 2


# ---- runtime half: lib/headless_env.sh ----------------------------------------


def bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", "-c", script], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}
    )


SRC = f'. "{LIB}"'


def test_headless_env_emits_assignment():
    r = bash(f"{SRC}; headless_env")
    assert r.returncode == 0
    assert "GOH_HEADLESS=1" in r.stdout


def test_headless_env_composes_with_env():
    r = bash(f"{SRC}; env $(headless_env) printenv GOH_HEADLESS")
    assert r.returncode == 0 and r.stdout.strip() == "1"


def test_require_live_refuses_with_distinct_exit_code():
    for truthy in ("1", "yes", "true", "on"):
        r = bash(f"{SRC}; GOH_HEADLESS={truthy} headless_require_live qa/sweep")
        assert r.returncode == 3, (truthy, r.returncode)
        assert "refusing" in r.stderr


def test_require_live_allows_attended_run():
    for falsey in ("0", "false", "no", "off"):
        r = bash(f"{SRC}; GOH_HEADLESS={falsey} headless_require_live qa/sweep")
        assert r.returncode == 0, (falsey, r.stderr)
    r = bash(f"{SRC}; unset GOH_HEADLESS; headless_require_live qa/sweep")
    assert r.returncode == 0


def test_enforced_predicate_matches_contract():
    script = (
        f"{SRC}; "
        'headless_enforced || echo U1; '
        'GOH_HEADLESS=0 headless_enforced || echo F1; '
        'GOH_HEADLESS=yes headless_enforced && echo E2'
    )
    r = bash(script)
    assert r.returncode == 0
    assert "U1" in r.stdout and "F1" in r.stdout and "E2" in r.stdout
