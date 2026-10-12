//! Unit tests for `rustlex`.

use super::*;

/// Lex `text` line by line from code, returning each line's `code` view.
fn code_of(text: &str) -> Vec<String> {
    let mut state = LexState::Code;
    text.split('\n')
        .map(|line| {
            let (lexed, next) = lex_line(line, state);
            state = next;
            lexed.code
        })
        .collect()
}

#[test]
fn a_string_is_emptied_in_code_and_kept_in_display() {
    let (lexed, state) = lex_line(r#"let s = "a [b] c";"#, LexState::Code);
    assert_eq!(lexed.code, r#"let s = "";"#);
    assert_eq!(lexed.display, r#"let s = "a [b] c";"#);
    assert_eq!(state, LexState::Code);
}

#[test]
fn a_slash_pair_inside_a_string_is_not_a_comment() {
    let (lexed, _) = lex_line(r#"let u = "http://x"; let v = 1; // tail"#, LexState::Code);
    assert_eq!(lexed.code, r#"let u = ""; let v = 1; "#);
}

#[test]
fn an_escaped_quote_does_not_close_the_string() {
    assert_eq!(code_of(r#"f("a\"b", c)"#), vec![r#"f("", c)"#]);
    assert_eq!(code_of(r#"f("a\\", c)"#), vec![r#"f("", c)"#]);
}

#[test]
fn a_string_carries_onto_the_next_line() {
    assert_eq!(
        code_of("let s = \"one\ntwo [ \";\nx"),
        vec!["let s = \"", "\";", "x"]
    );
}

#[test]
fn raw_strings_close_only_on_their_own_hash_count() {
    assert_eq!(
        code_of(r##"let s = r#"a "quoted" b"#;"##),
        vec![r#"let s = "";"#]
    );
    assert_eq!(
        code_of("let s = r##\"x\"# still\nin\"##; y"),
        vec!["let s = \"", "\"; y"]
    );
    assert_eq!(code_of(r#"let s = br"a\"; t"#), vec![r#"let s = b""; t"#]);
}

#[test]
fn a_raw_identifier_is_not_a_string() {
    assert_eq!(code_of("let r#type = bar;"), vec!["let r#type = bar;"]);
    assert_eq!(code_of("for"), vec!["for"]);
}

#[test]
fn character_literals_are_emptied_and_lifetimes_kept() {
    assert_eq!(
        code_of(r#"let q = '"'; let s = "x";"#),
        vec![r#"let q = ''; let s = "";"#]
    );
    assert_eq!(
        code_of(r"let b = '['; let e = '\''; let u = '\u{5b}';"),
        vec!["let b = ''; let e = ''; let u = '';"]
    );
    assert_eq!(
        code_of("fn f<'a>(x: &'a str) {}"),
        vec!["fn f<'a>(x: &'a str) {}"]
    );
    assert_eq!(code_of("'outer: loop {}"), vec!["'outer: loop {}"]);
}

#[test]
fn block_comments_nest_and_carry() {
    assert_eq!(code_of("a /* x /* y */ z\nstill */ b"), vec!["a ", " b"]);
    assert_eq!(code_of(r#"/* "not a string */ c"#), vec![" c"]);
}
