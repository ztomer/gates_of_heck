//! Tests for `screen.rs`.
//
// Extracted to their own file rather than kept inline for the same reason
// `qbittorrent.rs` was: the file sat at the 500-line cap with its tests inside
// it, so every edit to a test in it — and there is a screenshot test in here —
// risked the structural gate. Twice in one session a test edit did exactly
// that. The cost of the class was paid at commit time, in the gate, by the
// next change; the cost of the fix is one file.
use super::*;

fn scanner() -> Scanner {
    Scanner::compile().expect("built-in patterns compile")
}

#[test]
fn masker_preserves_lines_and_masks_strings() {
    let sc = mask_swift("let a = \"NSScreen.main\" // NSScreen\n/* NSScreen */\nNSScreen.main\n");
    let lines: Vec<&str> = sc.split('\n').collect();
    assert_eq!(lines.len(), 4);
    assert!(!lines[0].contains("NSScreen"));
    assert!(!lines[1].contains("NSScreen"));
    assert!(lines[2].contains("NSScreen.main"));
}

#[test]
fn masker_handles_triple_quotes_and_escapes() {
    let text =
        "let s = \"\"\"NSScreen\nstill string\"\"\"\nlet t = \"a\\\"NSScreen\"\nNSScreen.main\n";
    let masked = mask_swift(text);
    assert_eq!(masked.split('\n').count(), text.split('\n').count());
    assert!(masked
        .lines()
        .last()
        .unwrap_or("")
        .contains("NSScreen.main"));
    assert!(!masked.split('\n').next().unwrap_or("").contains("NSScreen"));
}

#[test]
fn type_positions_exempt_member_access_flags() {
    let sc = scanner();
    for line in [
        "let s: [NSScreen]",
        "let s: NSScreen?",
        "func f() -> NSScreen",
        "if x is NSScreen",
        "let d: Foo<NSScreen>",
    ] {
        assert_eq!(
            check_text(&sc, "a.swift", &format!("{line}\n")).len(),
            0,
            "{line}"
        );
    }
    assert_eq!(check_text(&sc, "a.swift", "NSScreen.main\n").len(), 1);
    assert_eq!(
        check_text(&sc, "a.swift", "let s = NSScreen.screens\n").len(),
        1
    );
}

#[test]
fn markers_and_comment_lines_pass() {
    let sc = scanner();
    assert_eq!(
        check_text(&sc, "a.swift", "NSScreen.main // screen-ok: fake\n").len(),
        0
    );
    assert_eq!(
        check_text(&sc, "a.swift", "// screen-ok: whole file\nNSScreen.main\n").len(),
        0
    );
    assert_eq!(
        check_text(&sc, "a.swift", "// NSScreen.main explains the rule\n").len(),
        0
    );
    assert_eq!(check_text(&sc, "a.swift", "NSScreen.main\n").len(), 1);
}

#[test]
fn python_executor_rule() {
    let sc = scanner();
    // Prose naming a command executes nothing.
    assert_eq!(
        check_text(&sc, "t.py", "# screencapture is banned\n").len(),
        0
    );
    assert_eq!(
        check_text(&sc, "t.py", "subprocess.run([\"screencapture\", \"-x\"])\n").len(),
        1
    );
    assert_eq!(check_text(&sc, "t.py", "import pyautogui\n").len(), 1);
    // Headless-contract files are out of scope entirely.
    let src = "GOH_HEADLESS = 1\nsubprocess.run([\"screencapture\"])\n";
    assert_eq!(check_text(&sc, "t.py", src).len(), 0);
}

#[test]
fn fnmatch_crosses_slashes_like_fnmatch() {
    // Expectations verified against `fnmatch.fnmatch` itself: `*`
    // crosses slashes (no `**` specialness), and `*/` still needs its
    // slash — `tests/**/*.swift` does NOT match `tests/x.swift`.
    let rx = regex::Regex::new(&fnmatch_to_regex("tests/**/*.swift")).expect("compiles");
    assert!(rx.is_match("tests/a/b.swift"));
    assert!(!rx.is_match("tests/x.swift"));
    assert!(!rx.is_match("tests/x.py"));
    let rx = regex::Regex::new(&fnmatch_to_regex("tests/*")).expect("compiles");
    assert!(rx.is_match("tests/deep/nested.swift"));
}
