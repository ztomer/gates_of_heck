use super::{added_tests, declares_test, refusal};

const ADDS_A_TEST: &str = "diff --git a/tests/test_x.py b/tests/test_x.py\n\
+++ b/tests/test_x.py\n\
@@ -0,0 +1,2 @@\n\
+def test_it_fails_without_the_fix() -> None:\n\
+    assert x()\n";

const ADDS_CODE: &str = "+++ b/src/x.py\n@@ -1 +1 @@\n+def helper() -> None:\n";

#[test]
fn each_language_declares_a_test_its_own_way() {
    assert!(declares_test("t.py", "    def test_x(self):"));
    assert!(declares_test("t.py", "async def test_x():"));
    assert!(declares_test("a.rs", "    #[test]"));
    assert!(declares_test("A.swift", "    func testItReds() throws {"));
    assert!(declares_test("a.test.ts", "  it(\"reds\", () => {"));
    assert!(declares_test("a.bats", "@test \"reds\" {"));
    assert!(!declares_test("a.py", "x = 'def test_x'"));
    assert!(!declares_test("a.md", "def test_x():"));
}

#[test]
fn a_diff_names_the_tests_it_adds_and_not_the_lines_it_removes() {
    assert_eq!(
        added_tests(ADDS_A_TEST),
        vec!["tests/test_x.py: def test_it_fails_without_the_fix() -> None:"]
    );
    assert_eq!(
        added_tests("+++ b/t.py\n-def test_gone():\n"),
        Vec::<String>::new()
    );
    assert_eq!(added_tests(ADDS_CODE), Vec::<String>::new());
}

#[test]
fn a_commit_adding_a_test_without_the_trailer_is_refused() {
    let why = refusal("feat: x\n\nbody\n", ADDS_A_TEST).expect("refused");
    assert!(
        why.contains("tests/test_x.py") && why.contains("Red-run:"),
        "{why}"
    );
}

#[test]
fn a_claim_is_hollow_only_when_it_is_the_whole_hollow_word() {
    // A prefix match refused real claims that merely BEGIN with a hollow word (GOH.REDRUNHOLLOW).
    for real in [
        "none is a name, and the test went red when it was dropped",
        "nonetheless the guard was removed and the test failed",
        "done_state returning early -- the test went red",
        "-U1 diff context: the parser read one line and the test failed",
        "yesterday's fallback restored -- red",
    ] {
        let msg = format!("feat: x\n\nbody\n\nRed-run: {real}\n");
        assert_eq!(refusal(&msg, ADDS_A_TEST), None, "{real:?} was refused");
    }
    for hollow in super::HOLLOW {
        for spelt in [
            hollow.to_string(),
            format!("{hollow}."),
            format!(" {} ", hollow.to_uppercase()),
        ] {
            let msg = format!("feat: x\n\nbody\n\nRed-run: {spelt}\n");
            assert!(refusal(&msg, ADDS_A_TEST).is_some(), "{spelt:?} passed");
        }
    }
}

#[test]
fn the_trailer_must_be_in_the_trailer_block_and_say_something() {
    let named = "feat: x\n\nbody\n\nRed-run: x() returning True -- the test failed\n";
    assert_eq!(refusal(named, ADDS_A_TEST), None);
    for hollow in ["n/a", "none", "yes", "-", "TBD"] {
        let msg = format!("feat: x\n\nbody\n\nRed-run: {hollow}\n");
        assert!(refusal(&msg, ADDS_A_TEST).is_some(), "{hollow} passed");
    }
    let above = "feat: x\n\nRed-run: x() returning True\n\nbody\n";
    assert!(
        refusal(above, ADDS_A_TEST).is_some(),
        "a trailer above the body passed"
    );
}

#[test]
fn a_commit_adding_no_test_needs_no_trailer() {
    assert_eq!(refusal("feat: x\n", ADDS_CODE), None);
}

/// A `Red-run:` that is there but unreadable -- a wrapped line spoils the block -- is named as
/// that, not as a missing claim (`ZoneWM`, 2026-10-12).
#[test]
fn a_red_run_in_an_unreadable_block_names_the_blocking_line() {
    let diff =
        "diff --git a/tests/test_x.py b/tests/test_x.py\n+++ b/tests/test_x.py\n+def test_new():\n";
    let message = "fix: a\n\nRed-run: a long line\nthat wrapped.\nCo-Authored-By: x\n";
    let why = refusal(message, diff).expect("refused");
    assert!(why.contains("`that wrapped.` is not"), "{why}");
}

/// `app_updates` de40622 (GOH.REDRUN): a Rust test renamed in place, so the diff ADDS only its `fn`
/// line and keeps `#[test]` as context. At `-U0` the attribute was never seen and the commit,
/// whose `Red-run:` sat in the body rather than the trailer block, was accepted.
const RENAMES_A_RUST_TEST: &str = concat!(
    "+++ b/crates/core/src/deps_tests.rs\n",
    "@@ -40,2 +40,2 @@\n",
    " #[test]\n",
    "-fn the_old_name() {\n",
    "+fn the_browser_fetch_types_no_answer_into_the_installer() {\n",
);

const DE40622_SHAPE: &str = "fix(deps): x\n\nbody\n\nRed-run: the old fetch script fails the \
test\n\nCo-Authored-By: C <c@x>\n";

#[test]
fn a_test_fn_under_a_context_attribute_is_an_added_test() {
    assert_eq!(
        added_tests(RENAMES_A_RUST_TEST),
        vec!["crates/core/src/deps_tests.rs: fn the_browser_fetch_types_no_answer_into_the_installer() {"]
    );
    let added_attr = "+++ b/a.rs\n@@ -1 +1,2 @@\n+#[test]\n+fn t() {}\n";
    assert_eq!(added_tests(added_attr).len(), 1, "one test, not two");
    let under_inline = "+++ b/a.rs\n@@ -1 +1 @@\n #[inline]\n+fn f() {}\n";
    assert_eq!(added_tests(under_inline), Vec::<String>::new());
}

#[test]
fn red_run_in_the_body_is_refused_and_in_the_trailer_block_passes() {
    assert!(refusal(DE40622_SHAPE, RENAMES_A_RUST_TEST).is_some());
    let in_block = "fix: x\n\nbody\n\nRed-run: the old fetch fails it\nCo-Authored-By: C <c@x>\n";
    assert_eq!(refusal(in_block, RENAMES_A_RUST_TEST), None);
}

/// GOH.GATEHONEST (3): a test whose SIGNATURE changed is the same test. Keyed by its declaration
/// line, `func testA() {` -> `func testA() throws {` read as a new test and asked for a `Red-run:`
/// (token-pool 85eb45c, TP.WIDGETFLAKE). Tests are keyed by name, per file.
#[test]
fn a_test_whose_signature_changed_is_the_same_test() {
    let swift = concat!(
        "diff --git a/T/LoginItemTests.swift b/T/LoginItemTests.swift\n",
        "--- a/T/LoginItemTests.swift\n",
        "+++ b/T/LoginItemTests.swift\n",
        "@@ -50,2 +50,3 @@\n",
        "-    func testTheToggleTurnsItBackOn() {\n",
        "+    func testTheToggleTurnsItBackOn() throws {\n",
        "+        let defaults = try freshDefaults()\n",
        "@@ -60,1 +61,1 @@\n",
        "-    @Test func reds() {\n",
        "+    @Test func reds() async throws {\n",
    );
    assert_eq!(added_tests(swift), Vec::<String>::new());
    let py =
        "--- a/t.py\n+++ b/t.py\n@@ -1 +1 @@\n-def test_x(self):\n+def test_x(self, tmp_path):\n";
    assert_eq!(added_tests(py), Vec::<String>::new());
    let rs = "+++ b/a.rs\n@@ -1,2 +1,2 @@\n #[test]\n-fn t() {\n+fn t() -> Result<(), E> {\n";
    assert_eq!(added_tests(rs), Vec::<String>::new());
    let js = "+++ b/a.test.ts\n@@ -1 +1 @@\n-it(\"reds\", () => {\n+it(\"reds\", async () => {\n";
    assert_eq!(added_tests(js), Vec::<String>::new());
}

#[test]
fn a_test_moved_to_another_hunk_is_the_same_test_and_a_new_name_is_new() {
    let moved = concat!(
        "+++ b/t.py\n",
        "@@ -1,1 +0,0 @@\n",
        "-def test_moved():\n",
        "@@ -90,0 +89,2 @@\n",
        "+def test_moved():\n",
        "+def test_brand_new():\n",
    );
    assert_eq!(added_tests(moved), vec!["t.py: def test_brand_new():"]);
    // A name removed from ANOTHER file does not excuse one added here.
    let elsewhere =
        "+++ b/a.py\n@@ -1 +0,0 @@\n-def test_x():\n+++ b/b.py\n@@ -0,0 +1 @@\n+def test_x():\n";
    assert_eq!(added_tests(elsewhere), vec!["b.py: def test_x():"]);
    // A plain fn that GAINS `#[test]` was no test before: it is a new one.
    let attr = "+++ b/a.rs\n@@ -1,1 +1,2 @@\n-fn t() {\n+#[test]\n+fn t() -> R {\n";
    assert_eq!(added_tests(attr).len(), 1);
}

#[test]
fn a_red_run_left_in_the_body_is_refused_naming_its_line_and_the_fix() {
    // GOH.REDRUNPLACE: one blank line above Co-Authored-By makes it body text to git; the old
    // refusal said the block "carries no `Red-run:`", which reads as missing. Both shapes.
    let above = "feat: x\n\nbody\n\nRed-run: dropped the guard, the test went red\n\n\
                 Co-Authored-By: A <a@b>\n";
    let inside = "feat: x\n\nbody line\nRed-run: dropped the guard, the test went red\nmore\n\n\
                  Co-Authored-By: A <a@b>\n";
    for (msg, line) in [(above, 5), (inside, 4)] {
        let why = refusal(msg, ADDS_A_TEST).expect("still refused: git reads no trailer there");
        assert!(
            why.contains(&format!(
                "Red-run found in the body (line {line}), not in the trailer block: put it \
                 directly above Co-Authored-By with no blank line between"
            )),
            "{why}"
        );
    }
    let missing = refusal("feat: x\n\nbody\n\nCo-Authored-By: A <a@b>\n", ADDS_A_TEST).unwrap();
    assert!(!missing.contains("found in the body"), "{missing}");
    let hollow = refusal(
        "feat: x\n\nbody\n\nRed-run: none\nCo-Authored-By: A <a@b>\n",
        ADDS_A_TEST,
    );
    assert!(
        !hollow.unwrap().contains("found in the body"),
        "a hollow trailer is not misplaced"
    );
}
