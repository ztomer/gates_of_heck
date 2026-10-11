use super::*;

/// The stylerc value of `key`, read the way the Python half reads it.
fn stylerc(key: &str) -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../tui/stylerc");
    let text = std::fs::read_to_string(path).unwrap_or_default();
    text.lines()
        .filter_map(|l| l.trim().split_once('='))
        .find(|(k, _)| k.trim() == key)
        .map(|(_, v)| v.trim().trim_matches('"').replace("\\033", "\u{1b}"))
        .unwrap_or_default()
}

#[test]
fn every_glyph_and_colour_is_the_stylercs() {
    let pairs = [
        ("ICON_START", START),
        ("ICON_STEP", STEP),
        ("ICON_OK", OK),
        ("ICON_ERR", ERR),
        ("ICON_WARN", WARN),
        ("C_RESET", C_RESET),
        ("C_GREEN", C_GREEN),
        ("C_RED", C_RED),
        ("C_YELLOW", C_YELLOW),
        ("C_GRAY", C_GRAY),
        ("C_DIM", C_DIM),
        ("C_BOLD", C_BOLD),
    ];
    for (key, got) in pairs {
        let want = stylerc(key);
        assert!(!want.is_empty(), "stylerc has no {key}");
        assert_eq!(got, want, "{key}");
    }
}

#[test]
fn plain_rendering_is_the_glyph_a_space_and_the_message() {
    assert_eq!(render(Mark::Ok, "clean", false), format!("{OK} clean"));
    assert_eq!(render(Mark::Err, "red", false), format!("{ERR} red"));
}

#[test]
fn coloured_rendering_matches_the_python_half() {
    assert_eq!(
        render(Mark::Ok, "m", true),
        format!("{C_GREEN}{OK}{C_RESET} m")
    );
    assert_eq!(
        render(Mark::Info, "m", true),
        format!("{C_GRAY}{START}{C_RESET} {C_DIM}m{C_RESET}")
    );
    assert_eq!(
        render(Mark::Step, "m", true),
        format!("{C_DIM}{STEP} m{C_RESET}")
    );
}

#[test]
fn no_color_set_to_anything_turns_colour_off() {
    use std::ffi::OsStr;
    assert!(colour_allowed(true, None));
    assert!(colour_allowed(true, Some(OsStr::new(""))));
    assert!(!colour_allowed(true, Some(OsStr::new("0"))));
    assert!(!colour_allowed(true, Some(OsStr::new("1"))));
    assert!(!colour_allowed(false, None));
}

#[test]
fn a_built_line_gives_back_its_mark() {
    assert_eq!(
        split_mark(&format!("{WARN} careful")),
        Some((Mark::Warn, "careful"))
    );
    assert_eq!(split_mark("plain text"), None);
    assert!(Mark::Err.to_stderr() && Mark::Warn.to_stderr() && !Mark::Ok.to_stderr());
}

#[test]
fn text_off_a_terminal_passes_through_unchanged() {
    // The test harness captures both streams, so neither is a terminal.
    let text = format!("{OK} a\nplain\n{ERR} b\n");
    assert_eq!(render_text(&text, Stream::Out), text);
}
