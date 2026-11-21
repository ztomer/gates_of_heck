//! The split walk reaches every shape of command: a whitespace-holding `F` used inside each
//! compound form the walk must descend into, and the forms it must read past without a verdict.

use super::split::findings;

/// The `(command, how many findings)` pairs: a whitespace-holding `F` used inside each compound
/// form the walk must descend into, and the forms it must read past without a verdict.
const WALKED: [(&str, usize); 13] = [
    // C-style `for ((;;))` body.
    (r#"F="a b"; for ((i=0; i<2; i++)); do git add $F; done"#, 1),
    // `case`: an arm with a body is read, an arm with none is not a fault.
    (r#"F="a b"; case x in y) ;; x) git add $F ;; esac"#, 1),
    // `if` / `elif` / `else`: the condition, the then, the elif's condition and body, the else.
    (
        r#"F="a b"; if git add $F; then echo $F; elif git rm $F; then echo $F; else echo $F; fi"#,
        5,
    ),
    // The else branch has no condition of its own.
    (r#"F="a b"; if true; then :; else git add $F; fi"#, 1),
    // `while` and `until`: condition and body.
    (r#"F="a b"; while git add $F; do echo $F; done"#, 2),
    (r#"F="a b"; until git add $F; do echo $F; done"#, 2),
    // A coprocess runs its command.
    (r#"F="a b"; coproc git add $F"#, 1),
    // `[[ ]]` and `(( ))` are not commands: no finding in them, and the walk goes on past them.
    (r#"F="a b"; [[ -n $F ]]; (( 1 + 1 )); git add $F"#, 1),
    (r#"F="a b"; [[ -n $F ]]"#, 0),
    // A process substitution is a list of commands (the parser yields it only after a command
    // word, never in a prefix, so only the suffix is walked).
    (r#"F="a b"; cat <(git add $F)"#, 1),
    // A declaration builtin given its assignment as one (quoted) word.
    (r#"export "F=a b"; git add $F"#, 1),
    (r#"declare "F=a b" "G=c"; git add $F"#, 1),
    // A declaration word that is no assignment sets nothing.
    (r#"F="a b"; export -p 1X=2; git add $F"#, 1),
];

#[test]
fn the_walk_descends_every_compound_form() {
    for (command, want) in WALKED {
        let found = findings(command);
        assert_eq!(found.len(), want, "{command}: {found:?}");
        assert!(found.iter().all(|f| f.var == "F" && f.value == "\"a b\""));
    }
}

#[test]
fn a_declared_word_assignment_clears_or_sets_the_name() {
    // `export "F=a b"` sets F; `export "F=one"` clears it again.
    assert_eq!(
        findings(r#"export "F=a b"; export "F=one"; git add $F"#),
        []
    );
    // A `NAME=` with an empty name, a leading digit or a bad character is not an assignment.
    for word in ["=a b", r#""1F=a b""#, r#""F-X=a b""#, r#""no equals""#] {
        let command = format!("export {word}; git add $F");
        assert_eq!(findings(&command), [], "{command}");
    }
}

#[test]
fn an_append_extends_a_spaced_value_and_a_plain_append_keeps_it() {
    let found = findings(r#"F="a b"; F+=" c"; git add $F"#);
    assert_eq!(found.len(), 1);
    assert_eq!(found[0].value, "\"a b\"\" c\"", "the appended text is kept");
    // Appending text without whitespace to a spaced name leaves it spaced.
    assert_eq!(findings(r#"F="a b"; F+=c; git add $F"#).len(), 1);
    // Appending to an unspaced name, with whitespace, makes it spaced.
    assert_eq!(findings(r#"F=a; F+=" b"; git add $F"#).len(), 1);
}

#[test]
fn an_array_element_assignment_is_no_scalar() {
    assert_eq!(findings(r#"F="a b"; F[1]="x y"; git add $F"#).len(), 1);
    assert_eq!(findings(r#"A[1]="x y"; git add $A"#), []);
}

#[test]
fn only_a_plain_expansion_counts() {
    // `${F:-x}`, `${#F}`, `${!F}` and `$(...)` are not the plain `$F` zsh leaves whole; a
    // whitespace-holding variable inside one is not a finding, nor is it a value's whitespace.
    for inner in ["F:-x", "#F", "!F"] {
        for command in [
            format!(r#"F="a b"; git add ${{{inner}}}"#),
            format!(r#"F="a b"; G=${{{inner}}}; git add $G"#),
        ] {
            assert_eq!(findings(&command), [], "{command}");
        }
    }
    for command in [
        r#"F="a b"; git add $(echo x)"#,
        r#"F="a b"; G=$(echo $F); git add $G"#,
    ] {
        assert_eq!(findings(command), [], "{command}");
    }
}

#[test]
fn a_word_the_parser_cannot_read_is_not_judged() {
    // An unterminated expansion parses as a command but not as a word: no verdict, no panic.
    for command in [r#"F="a b"; git add ${F"#, r#"F="a b"; git add "${F""#] {
        assert_eq!(findings(command), [], "{command}");
    }
    assert_eq!(findings(r#"F="a b"; G=${F; git add $G"#), []);
}

#[test]
fn a_function_body_and_its_declared_form_are_walked() {
    assert_eq!(findings(r#"F="a b"; f() { git add $F; }"#).len(), 1);
}
