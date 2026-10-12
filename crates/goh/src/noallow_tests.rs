//! Unit tests for `noallow` (split out for the line cap).

use super::*;

fn scanner() -> Scanner {
    Scanner::compile().expect("built-in patterns compile")
}

// The fixtures below are BUILT, never spelled: this file is policed by
// its own check, and until 2026-10-12 a suppression written inside a
// string literal fired (the frozen reference still does). Until 2026-09-23
// a backticked mention of the generated marker in the module doc
// exempted the whole file, which hid exactly these lines.
const ALLOW: &str = "allow";
const EXPECT: &str = "expect";

/// `#[kind(body)]`, or `#![kind(body)]` for an inner attribute.
fn attr(inner: bool, kind: &str, body: &str) -> String {
    format!("#{}[{kind}({body})]", if inner { "!" } else { "" })
}

/// `#[cfg_attr(cfg, wrapped)]`.
fn cfg_attr(cfg: &str, wrapped: &str) -> String {
    format!("#[{}({cfg}, {wrapped})]", "cfg_attr")
}

#[test]
fn literal_suppressions_fire() {
    let sc = scanner();
    for line in [
        attr(false, ALLOW, "dead_code"),
        attr(true, ALLOW, "clippy::all"),
        attr(false, EXPECT, "dead_code"),
        format!("    {}", attr(true, EXPECT, "unused")),
    ] {
        assert_eq!(sc.scan_code(&line).len(), 1, "{line}");
    }
}

#[test]
fn doc_comments_are_prose_not_suppressions() {
    let sc = scanner();
    let text = format!(
        "/// Mentioning {} is prose.\nfn f() {{}}\n/* {} */\n",
        attr(false, ALLOW, "dead_code"),
        attr(false, EXPECT, "x")
    );
    assert_eq!(sc.scan_code(&text).len(), 0);
    // A real attribute after a same-line block close still fires.
    assert_eq!(
        sc.scan_code(&format!("/* note */ {}", attr(false, ALLOW, "x")))
            .len(),
        1
    );
}

#[test]
fn nested_block_comments_carry_state() {
    let sc = scanner();
    let text = format!(
        "/* outer /* inner */ {}\nmore\n*/\n{}\n",
        attr(false, ALLOW, "x"),
        attr(false, ALLOW, "y")
    );
    let hits = sc.scan_code(&text);
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0].0, 4);
}

#[test]
fn cfg_attr_wrapped_suppressions_fire() {
    let sc = scanner();
    let macos = "target_os = \"macos\"";
    assert_eq!(
        sc.scan_code(&cfg_attr(macos, &format!("{EXPECT}(unsafe_code)")))
            .len(),
        1
    );
    // Multi-line rustfmt layout: the token on the continuation line.
    let text = format!(
        "#[{}(\n    {macos},\n    {ALLOW}(dead_code),\n)]\n",
        "cfg_attr"
    );
    assert_eq!(sc.scan_code(&text).len(), 1);
    // cfg_attr without a suppression passes.
    assert_eq!(sc.scan_code(&cfg_attr(macos, "must_use")).len(), 0);
    // A path or a longer identifier is not a suppression.
    assert_eq!(
        sc.scan_code(&cfg_attr("x", &format!("cfg::{ALLOW}(y)")))
            .len(),
        0
    );
    assert_eq!(sc.scan_code("let allow_x = 1;").len(), 0);
}

/// THE CLASS (6.4): text inside a literal is not an attribute, in any
/// literal's spelling, while a real attribute beside it is still one.
#[test]
fn a_suppression_inside_a_literal_is_not_one() {
    let sc = scanner();
    let quoted = attr(false, ALLOW, "x");
    for line in [
        format!("let s = \"{quoted}\";"),
        format!("let s = r#\"{quoted}\"#;"),
        format!("let s = b\"{quoted}\";"),
        format!("f(\"a\\\"{quoted}\");"),
        cfg_attr("x", &format!("\"{ALLOW}(y)\"")),
    ] {
        assert_eq!(sc.scan_code(&line).len(), 0, "{line}");
    }
    let beside = format!("let s = \"{quoted}\"; {}", attr(false, ALLOW, "dead_code"));
    assert_eq!(sc.scan_code(&beside).len(), 1, "{beside}");
    let after_url = format!("let u = \"http://x\"; {}", attr(false, EXPECT, "y"));
    assert_eq!(
        sc.scan_code(&after_url).len(),
        1,
        "a `//` in a string hid it"
    );
}

/// A raw string holding whole attribute lines spans lines: everything up
/// to its close is text, and the line after it is code again.
#[test]
fn a_raw_string_over_several_lines_is_text_to_its_close() {
    let sc = scanner();
    let text = format!(
        "let fixture = r##\"\n{}\nfn f() {{}}\n{}\n\"##;\n{}\n",
        attr(false, ALLOW, "dead_code"),
        cfg_attr("test", &format!("{EXPECT}(x)")),
        attr(true, ALLOW, "real"),
    );
    let hits = sc.scan_code(&text);
    assert_eq!(
        hits.iter().map(|h| h.0).collect::<Vec<_>>(),
        vec![6],
        "{hits:?}"
    );
}

/// A `cfg_attr` spelled inside a string never opens one: a later
/// `.expect(` is not inside any attribute.
#[test]
fn a_quoted_cfg_attr_does_not_stay_open() {
    let sc = scanner();
    let text = format!(
        "let s = \"{}\";\nlet v = w.{EXPECT}(\"fixture\");\n",
        cfg_attr("test", &format!("{ALLOW}(x)"))
    );
    assert_eq!(sc.scan_code(&text), vec![]);
}

/// A finding quotes the line as written, literals and all.
#[test]
fn a_finding_quotes_the_line_with_its_literals() {
    let sc = scanner();
    let line = cfg_attr("target_os = \"macos\"", &format!("{EXPECT}(unsafe_code)"));
    assert_eq!(sc.scan_code(&line), vec![(1, line.clone())]);
}

#[test]
fn a_documented_marker_is_not_the_marker() {
    assert!(is_generated("// @generated by a tool\nfn f() {}\n"));
    assert!(!is_generated(
        "//! Files carrying `@generated` are exempt.\nfn f() {}\n"
    ));
}
#[test]
fn scope_and_verdicts() {
    assert!(is_compiled_src("src/a.rs"));
    assert!(is_compiled_src("crates/x/tests/f.rs"));
    assert!(is_compiled_src("build.rs"));
    assert!(is_compiled_src("a/build.rs"));
    assert!(!is_compiled_src("tools/a.rs")); // Path scope only — the `.rs` suffix filter lives in `scan_root`, // mirroring the reference split between `_files` and // `_is_compiled_src`. assert!(is_compiled_src("src/a.py")); assert_eq!(classify(2, 3, true), ScopeVerdict::Violations); assert_eq!(classify(0, 0, true), ScopeVerdict::BlindLayout); assert_eq!(classify(0, 0, false), ScopeVerdict::Clean); assert_eq!(classify(0, 5, true), ScopeVerdict::Clean); }  #[test] fn generated_marker_exempts_within_forty_lines() { let head = (0..39) .map(|i| format!("// {i}")) .collect::<Vec<_>>() .join("\n"); assert!(scan_text( &scanner(), "f.rs", &format!("{head}\n// @generated\n#[allow(x)]") ) .len(), 0);
    let head = (0..40)
        .map(|i| format!("// {i}"))
        .collect::<Vec<_>>()
        .join("\n");
    assert_eq!(
        scan_text(
            &scanner(),
            "f.rs",
            &format!("{head}\n// @generated\n#[allow(x)]")
        )
        .len(),
        1
    );
}

/// The exemption is for GENERATED files. A hand-written source of ours that reads as generated is
/// one the gate silently skips -- which is what `noallow.rs` itself was until 2026-09-23. Every
/// `.rs` file in this workspace, read through the same predicate the gate uses.
#[test]
fn no_rust_source_of_ours_reads_as_generated() {
    fn walk(dir: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
        for entry in std::fs::read_dir(dir).expect("readable") {
            let path = entry.expect("entry").path();
            if path.is_dir() {
                walk(&path, out);
            } else if path.extension().is_some_and(|x| x == "rs") {
                out.push(path);
            }
        }
    }
    let crates = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("crates/")
        .to_path_buf();
    let mut files = Vec::new();
    walk(&crates, &mut files);
    assert!(files.len() > 50, "walked {} files", files.len());
    let exempt: Vec<_> = files
        .iter()
        .filter(|p| is_generated(&String::from_utf8_lossy(&std::fs::read(p).expect("read"))))
        .collect();
    assert_eq!(exempt, Vec::<&std::path::PathBuf>::new());
}
