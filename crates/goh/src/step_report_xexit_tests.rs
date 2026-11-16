//! `cross_lines` against hand cases and the fixture `tests/test_xexit.py` shares (GOH.XEXIT2). A
//! file of its own: a test module's embeds are not build inputs (`lib/goh_version.sh`
//! `goh_stamp_inputs_at`), and the stamp tells a test module by its file name.

use super::cross_lines;

#[test]
fn cross_lines_names_status_lines_only() {
    let esc = char::from(27); // a colour escape the step printed, no literal in the source
    let log = format!("ok\n  {esc}[31m✗{esc}[0m void hold\nrow: ✗ quoted\n⚠ advisory\n✗ second\n");
    assert_eq!(cross_lines(&log), vec!["✗ void hold", "✗ second"]);
    assert_eq!(
        cross_lines("⚠ advisory only\n✓ fine\n"),
        Vec::<String>::new()
    );
}

#[test]
fn cross_lines_judges_a_cr_progress_line_by_its_last_segment() {
    assert_eq!(cross_lines("...\r✗ x"), vec!["✗ x"]);
}

/// ONE fixture, read here and by `tests/test_xexit.py` (GOH.XEXIT2): the twins split alike.
#[test]
fn cross_lines_agrees_with_the_shared_fixture() {
    let raw = include_str!("../../../tests/fixtures/xexit/cases.json");
    let cases: Vec<serde_json::Value> = serde_json::from_str(raw).expect("fixture parses");
    assert_ne!(cases.len(), 0);
    for case in &cases {
        let text = case["text"].as_str().expect("text");
        let want: Vec<String> = case["crosses"]
            .as_array()
            .expect("crosses")
            .iter()
            .map(|v| v.as_str().expect("cross").to_owned())
            .collect();
        assert_eq!(cross_lines(text), want, "{}", case["name"]);
    }
}
