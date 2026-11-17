use super::*;

#[test]
fn todays_estate_sites_are_found() {
    for bad in [
        r#"    open -a "$DEST""#,
        r#"open "$APP""#,
        r#"  open "$DEST""#,
        r#"    open "$MENUBAR_APP""#,
        r#"        open "$BUNDLE_DIR""#,
        r#"[ "$was_running" = 1 ] && open -a "$DEST""#,
        r#"if x; then open "$APP"; fi"#,
        r#"/usr/bin/open -n "$APP""#,
        r#"( open "$APP" )"#,
        r#"exec open "$APP""#,
        "x || open -g Foo.app",
    ] {
        assert_eq!(scan_line(bad).len(), 1, "{bad}");
    }
}

/// GOH.BAREOPEN2: the shapes the first scan walked past, one plant each.
#[test]
fn the_shapes_the_first_scan_missed_are_found() {
    for bad in [
        // an `env` that sets variables (and clears nothing) hands the app the rest
        r#"env FOO=1 open -a "$APP""#,
        r#"  env FOO=1 BAR=2 /usr/bin/open "$APP""#,
        r#"sudo env X=1 open "$APP""#,
        r#"x=$(env -u HOME open "$APP")"#,
        // a variable assignment before the command is the same launch
        r#"FOO=1 open -a "$APP""#,
        // a command substitution inside a double-quoted string is code
        r#"out="$(open -a "$APP")""#,
        r#"echo "started: $(open "$APP")""#,
        r#"out="`open -a Foo`""#,
        r#"echo "$(echo hi | open -f)""#,
    ] {
        assert_eq!(scan_line(bad).len(), 1, "{bad}");
    }
}

#[test]
fn an_env_that_clears_the_environment_is_the_clean_launch() {
    for ok in [
        r#"env -i /usr/bin/open -a "$APP""#,
        r#"env -i HOME="$HOME" open "$APP""#,
        r#"env --ignore-environment open "$APP""#,
        r#"x="$(env -i open "$APP")""#,
        r#"echo "run env FOO=1 open -a X to launch""#,
        r#"x="$(echo open)""#,
        r#"msg="the file (open it) and $(date)""#,
    ] {
        assert!(scan_line(ok).is_empty(), "{ok}");
    }
}

#[test]
fn within_the_grace_the_scan_reads_as_the_last_release_did() {
    for old in [
        r#"open "$APP""#,
        "x || open -g Foo.app",
        r#"( open "$APP" )"#,
    ] {
        assert_eq!(scan_line_in(old, false).len(), 1, "{old}");
    }
    for new in [
        r#"env FOO=1 open -a "$APP""#,
        r#"FOO=1 open -a "$APP""#,
        r#"out="$(open -a "$APP")""#,
    ] {
        assert_eq!(scan_line_in(new, false).len(), 0, "{new}");
        assert_eq!(scan_line_in(new, true).len(), 1, "{new}");
    }
}

#[test]
fn a_word_that_is_not_the_command_passes() {
    for ok in [
        r#"info "launch:    open -a Divoom""#,
        "if [ -t 0 ] && command -v open &>/dev/null; then",
        "# open -a \"$DEST\"",
        "reopen \"$APP\"",
        "open_app \"$APP\"",
        "xdg-open \"$URL\"",
        "\"$GOH_DIR/bin/goh\" launch-app \"$APP\"",
        "echo opened",
        "f=$(gopen x)",
        "x.open y",
        r#"grep -qiE 'what is next|open work|the plan' <<<"$p" || continue"#,
        r#"die "export failed: open the trace in Instruments (open \"$T\")""#,
        r#"err "relaunch failed: open $DEST failed twice""#,
        r#"3) ok "installed (not running; open it once and it opens at login)" ;;"#,
    ] {
        assert!(scan_line(ok).is_empty(), "{ok}");
    }
}

#[test]
fn a_heredoc_body_is_not_read_and_the_line_after_it_is() {
    let text = "osascript << APPLESCRIPT 2>/dev/null || true\n  tell application \"Finder\"\n    open\n  end tell\nAPPLESCRIPT\nopen \"$APP\"\ncat <<-'EOF'\nopen x\n\tEOF\nn=$((1<<2))\nopen y\n";
    assert_eq!(scan_text(text), vec![(6, "open \"$APP\""), (11, "open y")]);
}
