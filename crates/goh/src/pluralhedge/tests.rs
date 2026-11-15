//! The finder over the estate's shapes and the look-alikes that must stay clean.

use super::scan::{findings, findings_in, hedges, Lang};

fn lines(src: &str, lang: Lang) -> Vec<usize> {
    findings(src, lang).into_iter().map(|f| f.line).collect()
}

#[test]
fn the_hedges_goh_printed_are_found() {
    let ies = ["entr", "(ies)"].concat();
    assert_eq!(hedges(&format!("0 open {ies}")), [ies.as_str()]);
    assert_eq!(hedges(&h("{n} step@ failed")), [h("step@")]);
    let es = ["process", "(es)"].concat();
    assert_eq!(hedges(&format!("6 {es}")), [es.as_str()]);
    assert_eq!(hedges(&h("{n} {unit}@, seeded")), [h("{unit}@")]);
    let py = h("err(f\"{bad} case@ wrong\")\nok('2 finding@')\n");
    assert_eq!(lines(&py, Lang::Hash), [1, 2]);
    assert_eq!(lines(&h("echo \"$n step@ ran\"\n"), Lang::Hash), [1]);
    assert_eq!(lines(&h("format!(\"{n} call@\")\n"), Lang::Slash), [1]);
}

#[test]
fn the_look_alikes_stay_clean() {
    assert_eq!(hedges("open an http(s) link").len(), 0);
    assert_eq!(hedges("console.log(s);").len(), 0);
    assert_eq!(hedges("m=importlib.util.module_from_spec(s);").len(), 0);
    assert_eq!(hedges("def close(s):").len(), 0);
    assert_eq!(hedges("(s) alone").len(), 0);
    let src =
        h("# 3 file@ in a comment\nx = 1  # don't count file@\n// call@\nlet c = 'a'; // x@\n");
    assert_eq!(lines(&src, Lang::Hash), Vec::<usize>::new());
    assert_eq!(lines(&src, Lang::Slash), Vec::<usize>::new());
}

#[test]
fn a_hedge_outside_a_string_is_not_one() {
    assert_eq!(lines(&h("for file@ in x:\n"), Lang::Hash).len(), 0);
    assert_eq!(lines("fn f(s: &str) {}\n", Lang::Slash).len(), 0);
}

/// `@` spelled as the hedge suffix: the fixtures below plant hedges, and goh's own tree is judged
/// by this finder (a planted hedge in a literal here would be a finding of its own).
fn h(s: &str) -> String {
    s.replace('@', "(s)")
}

#[test]
fn a_builtin_call_shown_in_a_string_is_not_a_hedge() {
    // A private Swift app's ZW.WALLCLOCK2 (2026-11-11): a checker's message showing the code it
    // refuses.
    assert_eq!(hedges(&h("call print@ instead of a bare write")).len(), 0);
    let py = h("MSG = \"replace `print@` with `out(line)`, keep `len@`\"\n");
    assert_eq!(lines(&py, Lang::Hash), Vec::<usize>::new());
    // A count noun that is also a builtin's name stays a hedge.
    assert_eq!(
        hedges(&h("{n} set@ and {m} list@")),
        [h("set@"), h("list@")]
    );
    assert_eq!(hedges(&h("{n} type@")), [h("type@")]);
}

#[test]
fn a_hedge_inside_a_command_substitution_in_a_string_is_seen() {
    // scripts tests/test_reclaim_session_space.sh:180 (SC-3, 2026-11-12): only a failing test saw it.
    let sh = h("t \"$([[ \"$out\" == *\"of which 5 idle session@\"* ]] && echo 1)\"\n");
    assert_eq!(lines(&sh, Lang::Hash), [1]);
    // The code of a substitution is not a string: a `(s)` there is not read.
    assert_eq!(lines(&h("x=\"$(f@ | wc -l) left\"\n"), Lang::Hash).len(), 0);
}

#[test]
fn an_identifier_call_shown_in_a_string_is_not_a_hedge() {
    // CadGoose2 tools/check_replies_are_sanitized.py (2026-11-11): a C fixture in a Python string.
    let py = h("raw = \"void reply() { stripThinkBlocks@; }\\n\"\n");
    assert_eq!(lines(&py, Lang::Hash), Vec::<usize>::new());
    // A type's name hedged is a noun hedge (necrohand Constants+Render.swift: `CAMetalLayer@`).
    assert_eq!(hedges(&h("3 CAMetalLayer@")), [h("CAMetalLayer@")]);
    assert_eq!(hedges(&h("x = trim@; }")).len(), 0); // a bare name amid code
    assert_eq!(hedges(&h("y = f@.strip()")).len(), 0);
    // The estate's real hedges next to code-like marks (a private Swift app, a private consumer,
    // ZeroThunder).
    assert_eq!(
        hedges(&h("modifier keycode@ {list(left)} held")),
        [h("keycode@")]
    );
    assert_eq!(hedges(&h("{n} engine cache@; ")), [h("cache@")]);
    assert_eq!(hedges(&h("{n} item@{tail}")), [h("item@")]);
    // Real noun hedges, capitalised, acronyms and at a sentence's end, are still found.
    assert_eq!(
        hedges(&h("File@ changed: 3 URL@.")),
        [h("File@"), h("URL@")]
    );
    assert_eq!(
        hedges(&h("(2 file@), then 1 entry@; done")),
        [h("file@"), h("entry@")]
    );
}

#[test]
fn a_hedge_on_the_continuation_line_of_a_string_is_seen() {
    // GOH.LINEHEDGE: goh's own step_report.rs printed "the x line@ above" on the line after a `\`
    // continuation, which the line-by-line lexer read as code. Rust and Python continue a string
    // through a trailing backslash; the next line starts inside it.
    let rs = h("let m = format!(\"{out}\\n\\\n    printed the x line@ above\\n\");\nlet n = 1;\n");
    let on = |src: &str, lang| -> Vec<usize> {
        findings_in(src, lang, true)
            .into_iter()
            .map(|f| f.line)
            .collect()
    };
    assert_eq!(on(&rs, Lang::Slash), [2]);
    // Within the grace (crate::grace) it reads as the last release did.
    assert_eq!(findings_in(&rs, Lang::Slash, false).len(), 0);
    let py = h("MSG = \"first \\\n    2 file@ wrong\"\nx = 'a'\n");
    assert_eq!(on(&py, Lang::Hash), [2]);
    // The continuation ends with the string: code after it is code again, and a string that
    // only ends its line (no backslash) never carries over.
    let after = h("let m = \"a \\\n b\"; // c@\nlet f = x.y@;\nlet c = '\"';\nlet d = e@;\n");
    assert_eq!(on(&after, Lang::Slash), Vec::<usize>::new());
}

/// A committed fixture whose one file carries 12 hedges, seeded at 12 in its own tracked seed.
fn twelve() -> (goh_testkit::Repo, crate::estate_seed::Seed) {
    let body: String = (1..=12)
        .map(|i| h(&format!("echo \"{i} file@\"\n")))
        .collect();
    let r = goh_testkit::repo_with(&[("a.sh", body.as_str())]).expect("fixture");
    r.git(&["commit", "-qm", "root"]).expect("commit");
    let root = crate::gitutil::root_commit(r.path()).expect("root commit");
    let seed = format!(
        r#"{{"entries":[{{"root":"{root}","repo":"x","path":"a.sh","count":12,"reason":"r"}}]}}"#
    );
    r.write(super::KIND.file, &seed).expect("seed");
    let seed = crate::estate_seed::load_from(r.path(), None, super::KIND).expect("loads");
    (r, seed)
}

#[test]
fn all_lists_every_site_of_a_seeded_file() {
    // GOH.HEDGELIST: the warning listed five and "... and 7 more", so the rest were unfindable.
    let (r, seed) = twelve();
    let files = vec!["a.sh".to_string()];
    let (code, _, warns) = super::judge_seeded(r.path(), &files, ("", false), &seed, true);
    assert_eq!(code, 0, "{warns}");
    for i in 1..=12 {
        assert!(warns.contains(&format!("a.sh:{i}:")), "site {i}: {warns}");
    }
    assert!(!warns.contains("more, counted"), "{warns}");
    let (_, _, short) = super::judge_seeded(r.path(), &files, ("", false), &seed, false);
    assert!(
        short.contains("a.sh:5:") && !short.contains("a.sh:6:"),
        "{short}"
    );
    assert!(
        short.contains("... and 7 more, counted in the seed (--all lists them)"),
        "{short}"
    );
}
