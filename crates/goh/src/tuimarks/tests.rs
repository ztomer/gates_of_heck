//! The finder over the estate's shapes, the look-alikes that stay clean, and the step's verdict.

use super::scan::{findings, Lang};
use super::{exempt, judge_at, GOH_ROOT};

/// A CSI as source text spells it: a backslash, `code` (`033`, `e`, `x1b`), then `[`.
fn esc(code: &str) -> String {
    format!("\\{code}[")
}

fn lines(src: &str, lang: Lang) -> Vec<usize> {
    findings(src, lang)
        .expect("patterns compile")
        .into_iter()
        .map(|f| f.line)
        .collect()
}

#[test]
fn the_shapes_koffee_big_hand_rolled_are_found() {
    // KB.TUILIB (2026-11-12): tools/setup_android.sh and tools/verify_apk.sh.
    // The escapes are assembled (`esc`): goh's own status-markers scan reads this file.
    let sh = format!(
        "echo \"✗ adb not found\"\nprintf '  ✓ %s\\n' \"$x\"\n  echo -e \"{}32mdone\"\n\
         [ -f x ] && echo '→ building'\nprintf -- \"⚠ slow\"\necho $'{}1mbold'\necho · step\n",
        esc("033"),
        esc("e")
    );
    // Lines 3 and 6 open with an ANSI escape: `goh status-markers` owns that token (crate::shapes),
    // so this scanner no longer counts the same site a second time.
    assert_eq!(lines(&sh, Lang::Shell), [1, 2, 4, 5, 7]);
    let py = format!(
        "print(\"✓ done\")\n    print(f\"  ✗ {{name}}\", file=sys.stderr)\nprint('{}31mred')\n\
         print(\"\"\"⚠ slow\"\"\")\n",
        esc("x1b")
    );
    assert_eq!(
        lines(&py, Lang::Python),
        [1, 2, 4],
        "line 3 is an escape: status-markers'"
    );
    let escapes = format!(
        "echo \"{}32m done\"\nprint(\"{}31mred\")\n",
        esc("033"),
        esc("x1b")
    );
    assert!(
        crate::statusmark::scan::findings(&escapes).is_ok_and(|f| f.len() == 2),
        "status-markers judges the escapes tui-marks leaves"
    );
    let marks: Vec<String> = findings("echo '✗ x'\n", Lang::Shell)
        .expect("patterns compile")
        .into_iter()
        .map(|f| f.mark)
        .collect();
    assert_eq!(marks, ["✗"]);
}

#[test]
fn the_look_alikes_stay_clean() {
    let sh = "# echo \"✓ quoting the rule\"\nok \"built\"\necho \"done ✓\"\necho \"$OK x\"\n\
              info \"→ next\"\nfecho \"✓\"\nprintf '%s\\n' \"✓ data\"\n";
    assert_eq!(lines(sh, Lang::Shell), Vec::<usize>::new());
    let py = "# print(\"✓ x\")\nok(\"✓ built\")\nprint(f\"{ICON_OK} done\")\nprint(\"done ✓\")\n\
              blueprint(\"✓\")\n";
    assert_eq!(lines(py, Lang::Python), Vec::<usize>::new());
    // Each language's shape is that language's: a shell echo in Python is not read, nor the reverse.
    assert_eq!(lines("print(\"✓ x\")\n", Lang::Shell), Vec::<usize>::new());
    assert_eq!(lines("echo \"✓ x\"\n", Lang::Python), Vec::<usize>::new());
}

#[test]
fn a_reasoned_escape_on_the_line_is_not_a_finding() {
    let sh = "    warn() { printf '⚠ %s\\n' \"$*\" >&2; }  # tui-mark-ok: no lib.sh here\n\
              echo \"✗ x\"  # tui-mark-ok:\necho \"✗ y\"  # tui-mark-ok: \n";
    assert_eq!(
        lines(sh, Lang::Shell),
        [2, 3],
        "an escape with no reason is no escape"
    );
}

#[test]
fn a_git_hook_is_the_bootstrap_path() {
    assert!(exempt("0123abcd", ".githooks/pre-commit"));
    assert!(exempt("0123abcd", "hooks/commit-msg"));
    assert!(
        !exempt("0123abcd", "hooks/claude/skill_edit.sh"),
        "a Claude hook is not git's"
    );
    assert!(
        !exempt("0123abcd", "tools/hooks/pre-commit"),
        "only at the top level"
    );
    assert!(
        !exempt("0123abcd", ".githooks/helper.sh"),
        "a helper beside them is not a hook"
    );
}

#[test]
fn only_gohs_own_lib_is_exempt() {
    assert!(exempt(GOH_ROOT, "tui/lib.sh"));
    assert!(exempt(GOH_ROOT, "tui/lib.py"));
    assert!(!exempt(GOH_ROOT, "scripts/x.sh"), "goh's other scripts");
    assert!(
        !exempt("0123abcd", "tui/lib.sh"),
        "a consumer's vendored copy"
    );
}

fn planted() -> goh_testkit::Repo {
    let r = goh_testkit::repo_with(&[
        ("tools/setup.sh", "#!/bin/bash\necho \"✗ adb not found\"\n"),
        ("clean.sh", "#!/bin/bash\nok \"built\"\n"),
    ])
    .expect("fixture");
    r.git(&["commit", "-qm", "root"]).expect("commit");
    r
}

#[test]
fn an_unseeded_mark_is_red_once_the_grace_is_over() {
    let r = planted();
    let files = vec!["tools/setup.sh".to_string(), "clean.sh".to_string()];
    let (code, _, err) = judge_at(r.path(), &files, "", false, true);
    assert_eq!(code, 1, "{err}");
    assert!(err.contains("tools/setup.sh:2:"), "{err}");
    assert!(err.contains("info/ok/warn/err"), "{err}");
    let (code, _, _) = judge_at(r.path(), &["clean.sh".to_string()], "", false, true);
    assert_eq!(code, 0, "a clean file passes");
}

#[test]
fn within_the_grace_nothing_is_judged() {
    let r = planted();
    let (code, out, err) = judge_at(r.path(), &["tools/setup.sh".to_string()], "", false, false);
    assert_eq!((code, out.as_str(), err.as_str()), (0, "", ""));
}
