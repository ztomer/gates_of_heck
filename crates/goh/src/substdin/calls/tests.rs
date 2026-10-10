//! The scanner's specification, one test per shape the estate actually uses.
//!
//! Every case is calibrated in BOTH directions: each `#[test]` was watched going red with
//! the scanner broken, so a green run is evidence about the rule and not about a scanner
//! that quietly stopped finding anything.

use super::*;

/// One `call:why` per finding, so an assertion compares plain strings and the failure
/// message reads as the rule rather than as a borrow error.
fn pairs(source: &str) -> Vec<String> {
    scan_source(source)
        .unwrap_or_else(|e| panic!("parses: {e}"))
        .0
        .iter()
        .map(|f| format!("{}:{}", f.call, f.why.as_str()))
        .collect()
}

/// THE SHIPPED DEFECT: the md5 probe exactly as it hung the metarepo on 2026-10-05.
const SHIPPED: &str = "import subprocess\ndef _md5_cmd():\n    for candidate in (\"md5sum\", \"md5\", \"md5 -q\"):\n        probe = subprocess.run(candidate.split(), capture_output=True, text=True, check=False)\n";

#[test]
fn the_shipped_md5_probe_is_caught() {
    let (got, seen) = scan_source(SHIPPED).unwrap_or_else(|e| panic!("parses: {e}"));
    assert_eq!((got.len(), seen), (1, 1), "{got:?}");
    assert_eq!(got[0].why, Why::Inherits);
    assert_eq!(got[0].call, "subprocess.run");
    assert_eq!(got[0].line, 4);
    // The allowlist is keyed on this text, so it must be the CALL's line.
    assert!(
        got[0].text.contains("probe = subprocess.run"),
        "{}",
        got[0].text
    );
}

#[test]
fn the_three_legal_spellings_are_clean() {
    for source in [
        "import subprocess\nsubprocess.run(['md5'], stdin=subprocess.DEVNULL)\n",
        "import subprocess\nsubprocess.run(['md5'], input='')\n",
        // Inherit on purpose: the point is that it was WRITTEN DOWN.
        "import subprocess\nsubprocess.call(['x'], stdin=None)\n",
    ] {
        assert_eq!(pairs(source), Vec::<String>::new(), "{source}");
    }
}

#[test]
fn a_clean_tree_still_reports_its_call_count() {
    // The floor's subject: a repo that declares every stdin reports zero FINDINGS and a
    // non-zero call count, which is the difference between clean and blind.
    let (found, seen) = scan_source(
        "import subprocess\nsubprocess.run(['a'], stdin=None)\nsubprocess.run(['b'], input='')\n",
    )
    .unwrap_or_else(|e| panic!("parses: {e}"));
    assert_eq!(found.len(), 0, "{found:?}");
    assert_eq!(seen, 2);
}

#[test]
fn aliasing_is_seen_through_in_both_directions() {
    assert_eq!(
        pairs("import subprocess as sp\nsp.Popen(['cat'])\n"),
        ["subprocess.Popen:inherits"]
    );
    assert_eq!(
        pairs("from subprocess import check_output as co\nco(['cat'])\n"),
        ["subprocess.check_output:inherits"]
    );
    // An aliased import that DOES declare stdin is clean, or the aliasing would only
    // ever add findings and the sweep would learn to write exemptions instead.
    assert_eq!(
        pairs("import subprocess as sp\nsp.run(['x'], stdin=None)\n"),
        Vec::<String>::new()
    );
}

#[test]
fn kwargs_alone_is_a_finding_because_a_static_read_cannot_see_it() {
    assert_eq!(
        pairs("import subprocess\ndef f(**kw):\n    subprocess.run(['x'], **kw)\n"),
        ["subprocess.run:inherits"]
    );
}

#[test]
fn the_undeclorable_pair_is_rejected_outright() {
    assert_eq!(
        pairs("import os\nos.system('cat')\n"),
        ["os.system:undeclarable"]
    );
    assert_eq!(
        pairs("import os\nos.popen('cat')\n"),
        ["os.popen:undeclarable"]
    );
    assert_eq!(
        pairs("import subprocess\nsubprocess.getoutput('cat')\n"),
        ["subprocess.getoutput:undeclarable"]
    );
    assert_eq!(
        pairs("import subprocess\nsubprocess.getstatusoutput('cat')\n"),
        ["subprocess.getstatusoutput:undeclarable"]
    );
}

#[test]
fn asyncio_is_covered() {
    assert_eq!(
        pairs("import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec('cat')\n"),
        ["asyncio.create_subprocess_exec:inherits"]
    );
    assert_eq!(
        pairs(
            "import asyncio\nasync def f():\n    await asyncio.create_subprocess_shell('c', stdin=None)\n"
        ),
        Vec::<String>::new()
    );
}

#[test]
fn an_unrelated_run_is_not_a_process_call() {
    assert_eq!(
        pairs("class A:\n    def run(self): pass\nA().run()\n"),
        Vec::<String>::new()
    );
    // A deep attribute chain is deliberately not followed (see the module docstring):
    // the reference resolved one level and reaching further invents findings.
    assert_eq!(
        pairs("import subprocess\nclass W:\n    def run(self): pass\nW().run()\n"),
        Vec::<String>::new()
    );
    assert_eq!(
        scan_source("class A:\n    def run(self): pass\nA().run()\n")
            .unwrap_or_else(|e| panic!("{e}"))
            .1,
        0
    );
}

#[test]
fn a_call_named_in_a_string_or_docstring_is_not_a_call() {
    let source = "import subprocess\n\nRULES = '''\nsubprocess.run(['cat'])\nos.system('x')\n'''\n\"\"\"os.system is banned.\"\"\"\n";
    assert_eq!(pairs(source), Vec::<String>::new(), "{:?}", pairs(source));
}

#[test]
fn an_unparseable_file_is_an_error_never_a_silent_skip() {
    assert!(
        scan_source("def (\n").is_err(),
        "an unreadable file must not pass as clean"
    );
}

#[test]
fn two_findings_on_one_line_are_both_reported() {
    let (found, seen) = scan_source("import os\nos.system('a') or os.popen('b')\n")
        .unwrap_or_else(|e| panic!("{e}"));
    assert_eq!((found.len(), seen), (2, 2), "{found:?}");
    // One line, so one allowlist text for two call sites -- which is why an entry
    // permits exactly ONE call (see allow.rs).
    assert_eq!(found[0].text, found[1].text);
}
