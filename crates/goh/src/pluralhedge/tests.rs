//! The finder over the estate's shapes and the look-alikes that must stay clean.

use super::scan::{findings, hedges, Lang};

fn lines(src: &str, lang: Lang) -> Vec<usize> {
    findings(src, lang).into_iter().map(|f| f.line).collect()
}

#[test]
fn the_hedges_goh_printed_are_found() {
    assert_eq!(hedges("0 open entr(ies)"), ["entr(ies)"]);
    assert_eq!(hedges("{n} step(s) failed"), ["step(s)"]);
    assert_eq!(hedges("6 process(es)"), ["process(es)"]);
    assert_eq!(hedges("{n} {unit}(s), seeded"), ["{unit}(s)"]);
    let py = "err(f\"{bad} case(s) wrong\")\nok('2 finding(s)')\n";
    assert_eq!(lines(py, Lang::Hash), [1, 2]);
    assert_eq!(lines("echo \"$n step(s) ran\"\n", Lang::Hash), [1]);
    assert_eq!(lines("format!(\"{n} call(s)\")\n", Lang::Slash), [1]);
}

#[test]
fn the_look_alikes_stay_clean() {
    assert_eq!(hedges("open an http(s) link").len(), 0);
    assert_eq!(hedges("console.log(s);").len(), 0);
    assert_eq!(hedges("m=importlib.util.module_from_spec(s);").len(), 0);
    assert_eq!(hedges("def close(s):").len(), 0);
    assert_eq!(hedges("(s) alone").len(), 0);
    let src = "# 3 file(s) in a comment\nx = 1  # don't count file(s)\n// call(s)\nlet c = 'a'; // x(s)\n";
    assert_eq!(lines(src, Lang::Hash), Vec::<usize>::new());
    assert_eq!(lines(src, Lang::Slash), Vec::<usize>::new());
}

#[test]
fn a_hedge_outside_a_string_is_not_one() {
    assert_eq!(lines("for file(s) in x:\n", Lang::Hash).len(), 0);
    assert_eq!(lines("fn f(s: &str) {}\n", Lang::Slash).len(), 0);
}
