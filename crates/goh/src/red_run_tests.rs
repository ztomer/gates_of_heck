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
