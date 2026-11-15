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

/// The lines whose opener is `goh status-markers`' token, judged here as its fallback.
fn fallback(src: &str, lang: Lang) -> Vec<usize> {
    findings(src, lang)
        .expect("patterns compile")
        .into_iter()
        .filter(|f| f.fallback)
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
    // Lines 3 and 6 open with an ANSI escape: `goh status-markers`' token, which this scanner
    // judges as its FALLBACK, where status-markers does not judge the line (GOH.ESCAPEOWNER).
    assert_eq!(lines(&sh, Lang::Shell), [1, 2, 3, 4, 5, 6, 7]);
    assert_eq!(
        fallback(&sh, Lang::Shell),
        [3, 6],
        "the escapes are the fallback's"
    );
    let py = format!(
        "print(\"✓ done\")\n    print(f\"  ✗ {{name}}\", file=sys.stderr)\nprint('{}31mred')\n\
         print(\"\"\"⚠ slow\"\"\")\n",
        esc("x1b")
    );
    assert_eq!(lines(&py, Lang::Python), [1, 2, 3, 4]);
    assert_eq!(fallback(&py, Lang::Python), [3], "line 3 is an escape");
    let escapes = format!(
        "echo \"{}32m done\"\nprint(\"{}31mred\")\n",
        esc("033"),
        esc("x1b")
    );
    assert!(
        crate::statusmark::scan::findings(&escapes).is_ok_and(|f| f.len() == 2),
        "status-markers judges the same escapes, where it runs"
    );
    let marks: Vec<String> = findings("echo '✗ x'\n", Lang::Shell)
        .expect("patterns compile")
        .into_iter()
        .map(|f| f.mark)
        .collect();
    assert_eq!(marks, ["✗"]);
}

#[test]
fn a_bracketed_marker_opening_a_print_is_the_fallbacks_too() {
    let sh = concat!(
        "echo \"[ ",
        "OK ] built\"\nprintf '[ ",
        "==> ] %s\\n' \"$x\"\n",
        "echo \"[OK] a list\"\nRED='",
        "\\",
        "033[31m'\necho \"done ",
        "\\",
        "033[0m\"\n"
    );
    assert_eq!(
        fallback(sh, Lang::Shell),
        [1, 2],
        "an unpadded `[OK]`, an escape held in a variable and one after the text open no print"
    );
    let py = concat!("print(\"[ ", "Wrn ] slow\")\n");
    assert_eq!(fallback(py, Lang::Python), [1]);
}

#[test]
fn the_look_alikes_stay_clean() {
    let sh = "# echo \"✓ quoting the rule\"\nok \"built\"\necho \"done ✓\"\necho \"$OK x\"\n\
              info \"→ next\"\nfecho \"✓\"\nprintf '%s\\n' \"✓ data\"\n";
    assert_eq!(lines(sh, Lang::Shell), Vec::<usize>::new());
    let py = concat!(
        "# print(\"✓ x\")\nok(\"✓ built\")\nprint(f\"{ICON_OK} done\")\nprint(\"done ✓\")\n",
        "blueprint(\"✓\")\n\"\"\"The planted `print(\"[ ",
        "Ok  ] done\")` is red, as `print(\"✓ x\")` is.\"\"\"\n"
    );
    assert_eq!(
        lines(py, Lang::Python),
        Vec::<usize>::new(),
        "a print in backticks is a docstring quoting it"
    );
    assert_eq!(
        lines("x.print(\"✓ x\")\n", Lang::Python),
        [1],
        "a method print still is one"
    );
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

/// A repository with `files`, committed, its gate running `goh status-markers` when `opted_in`,
/// and every file it tracks.
fn repo(files: &[(&str, &str)], opted_in: bool) -> (goh_testkit::Repo, Vec<String>) {
    let steps = (
        ".gatesrc",
        "GOH_CI_STEPS='\"$GOH_DIR/gates/structural.sh\" --full:\"$GOH_DIR/gates/goh.sh\" status-markers'\n",
    );
    let mut all = files.to_vec();
    if opted_in {
        all.push(steps);
    }
    let r = goh_testkit::repo_with(&all).expect("fixture");
    r.git(&["commit", "-qm", "root"]).expect("commit");
    (r, listed(&all))
}

fn listed(files: &[(&str, &str)]) -> Vec<String> {
    files.iter().map(|(rel, _)| (*rel).to_string()).collect()
}

/// A planted escape opening a print, in a shell script status-markers reads (`.sh`) and in one
/// it does not (no extension, a shebang).
fn escapes() -> [(&'static str, String); 2] {
    [
        (
            "tools/setup.sh",
            format!("#!/bin/bash\necho -e \"{}32mdone\"\n", esc("033")),
        ),
        (
            "bin/run",
            format!("#!/bin/sh\nprintf '{}1m%s\\n' x\n", esc("e")),
        ),
    ]
}

#[test]
fn an_escape_is_red_where_status_markers_does_not_run() {
    // Calibration (GOH.ESCAPEOWNER): before this the escape was judged in no repo that had not
    // opted in to status-markers, and this fixture was green.
    let planted = escapes();
    let files: Vec<(&str, &str)> = planted.iter().map(|(r, t)| (*r, t.as_str())).collect();
    let (r, tracked) = repo(&files, false);
    let (code, _, err) = judge_at(r.path(), &tracked, "", false, true);
    assert_eq!(code, 1, "{err}");
    assert!(err.contains("tools/setup.sh:2:"), "{err}");
    assert!(err.contains("bin/run:2:"), "{err}");
}

#[test]
fn where_status_markers_runs_each_escape_is_reported_once() {
    let planted = escapes();
    let files: Vec<(&str, &str)> = planted.iter().map(|(r, t)| (*r, t.as_str())).collect();
    let (r, tracked) = repo(&files, true);
    assert!(super::status_markers_runs(r.path(), &tracked, false));
    let (code, _, err) = judge_at(r.path(), &tracked, "", false, true);
    // tools/setup.sh is status-markers' to count, once; bin/run is a file status-markers does not
    // read (no source extension), so this scanner keeps it and nobody leaves a hole.
    let by_status = crate::statusmark::scan::findings(&planted[0].1).expect("compiles");
    assert_eq!(by_status.len(), 1, "status-markers reports the escape");
    assert!(
        !err.contains("tools/setup.sh"),
        "tui-marks defers to status-markers: {err}"
    );
    assert!(crate::statusmark::scan::findings(&planted[1].1)
        .is_ok_and(|f| f.len() == 1 && !crate::statusmark::is_source(planted[1].0)));
    assert_eq!(code, 1, "{err}");
    assert!(err.contains("bin/run:2:"), "{err}");
}

#[test]
fn a_kare_mark_stays_this_scanners_where_status_markers_runs() {
    let files = [("tools/setup.sh", "#!/bin/bash\necho \"✗ adb not found\"\n")];
    let (r, tracked) = repo(&files, true);
    let (code, _, err) = judge_at(r.path(), &tracked, "", false, true);
    assert_eq!(code, 1, "{err}");
    assert!(err.contains("tools/setup.sh:2:"), "{err}");
}

#[test]
fn status_markers_runs_only_when_a_push_runs_it() {
    let gate = ("tools/gate.sh", "#!/bin/bash\n./gates/lint.sh\n");
    let cases: [(&[(&str, &str)], bool); 6] = [
        (
            &[(
                "tools/gate.sh",
                "#!/bin/bash\n\"$GOH_DIR/bin/goh\" status-markers\n",
            )],
            true,
        ),
        (&[gate, ("gates/lint.sh", "goh status-markers\n")], true),
        (
            &[(
                "tools/gate.sh",
                "#!/bin/bash\n# goh status-markers, someday\n",
            )],
            false,
        ),
        (
            &[
                gate,
                (
                    "gates/lint.sh",
                    "case $1 in fixed-sleep|status-markers|x) ;; esac\n",
                ),
            ],
            false,
        ),
        (&[("tools/other.sh", "goh status-markers\n")], false),
        (
            &[(
                ".gatesrc",
                "GOH_CI_STEPS='\"$GOH_DIR/gates/structural.sh\" --full'\n",
            )],
            false,
        ),
    ];
    for (files, want) in cases {
        let r = goh_testkit::repo_with(files).expect("fixture");
        assert_eq!(
            super::status_markers_runs(r.path(), &listed(files), false),
            want,
            "{files:?}"
        );
    }
}

#[test]
fn within_the_grace_nothing_is_judged() {
    let r = planted();
    let (code, out, err) = judge_at(r.path(), &["tools/setup.sh".to_string()], "", false, false);
    assert_eq!((code, out.as_str(), err.as_str()), (0, "", ""));
}
