//! The native file scanners that postdate `cli.rs` -- `home-paths`,
//! `no-allow` and `screen` -- end to end over fixture git repos, both
//! scopes, every exemption each one implements, and the git failure the
//! enumeration must report rather than read as an empty tree. Driven as a
//! process so `cargo llvm-cov` sees the `commands::run_*` entry points.
//! Suppression attributes and generation markers are ASSEMBLED at runtime:
//! written literally they would trip (or exempt) this very file.

// Test-only crate: helpers `expect` fixture writes, and a fixture that
// cannot be built is a broken test, not a result.
#![cfg(test)]

use goh_testkit::{goh_at, repo_with, Out, Repo};

fn goh(repo: Option<&Repo>, args: &[&str], envs: &[(&str, &str)]) -> Out {
    goh_at(
        std::path::Path::new(env!("CARGO_BIN_EXE_goh")),
        repo,
        args,
        envs,
    )
    .expect("goh runs")
}

/// The wrapper attribute's name, so no line here OPENS one: the scanner
/// is line-based and string-naive, and an opener whose closing bracket
/// sits inside a string literal would leave every later line "inside" it.
const CFG_ATTR: &str = "cfg_attr";

/// `#[<kind>(<body>)]`, built so this source never holds the attribute.
fn attr(kind: &str, body: &str) -> String {
    format!("#[{kind}({body})]")
}

// ---------------------------------------------------------------- home-paths

#[test]
fn home_paths_reports_suppresses_and_excludes() {
    let r = repo_with(&[
        ("src/a.py", "x = \"/Users/me/src/x\"\n"),
        ("src/b.sh", "cd ~/Projects/thing\n"),
        (
            "src/c.py",
            "# path-ok: recorded incident, kept verbatim\ny = \"/home/me/x/\"\n",
        ),
        ("docs/ok.md", "nothing here\n"),
    ])
    .expect("fixture");
    let out = goh(Some(&r), &["home-paths"], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out
        .stdout
        .contains("HARD-CODED HOME PATH in 2 location(s) (tracked)"));
    assert!(out.stdout.contains("src/a.py:1:6: macOS home path"));
    assert!(out.stdout.contains("src/b.sh:1:4: tilde checkout path"));
    assert!(!out.stdout.contains("src/c.py"), "path-ok must exempt");
    let out = goh(Some(&r), &["home-paths", "--exclude", "^src/(a|b)"], &[]);
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stdout
            .contains("✓ [no_home_paths] OK — 2 tracked files clean"),
        "{}",
        out.text()
    );
    let out = goh(Some(&r), &["home-paths", "--exclude", "("], &[]);
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
    assert!(out.stderr.contains("bad --exclude regex"), "{}", out.text());
}

#[test]
fn home_paths_staged_reads_the_index_not_the_worktree() {
    let r = repo_with(&[("tool.sh", "cd $HOME/Projects/x\n")]).expect("fixture");
    // The index holds the path; fixing only the worktree must not pass.
    r.write("tool.sh", "cd \"$(dirname \"$0\")\"\n")
        .expect("write");
    let out = goh(Some(&r), &["home-paths", "--staged"], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out.stdout.contains("(staged)"), "{}", out.text());
    assert!(out.stdout.contains("tool.sh:1:4: HOME checkout path"));
    // Full scope reads the worktree: clean.
    let out = goh(Some(&r), &["home-paths"], &[]);
    assert!(out.status.success(), "{}", out.text());
    // Stage the fix: the index is clean now.
    r.git(&["add", "tool.sh"]).expect("git");
    let out = goh(Some(&r), &["home-paths", "--staged"], &[]);
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stdout.contains("1 staged files clean"),
        "{}",
        out.text()
    );
    // Nothing staged at all is an honest pass that SAYS so.
    r.git(&["commit", "-qm", "x"]).expect("git");
    let out = goh(Some(&r), &["home-paths", "--staged"], &[]);
    assert!(out.status.success(), "{}", out.text());
    assert!(out.stdout.contains("nothing staged — 0 files to check"));
}

#[test]
fn home_paths_refuses_to_call_zero_files_clean() {
    let r = repo_with(&[("a.md", "x\n")]).expect("fixture");
    // Everything excluded at full scope: a refusal, never compliance.
    let out = goh(Some(&r), &["home-paths", "--exclude", "."], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out
        .stdout
        .contains("refusing to report clean over zero files"));
}

#[test]
fn home_paths_skips_binary_and_vanished_files_and_caps_the_report() {
    let many: Vec<String> = (0..205)
        .map(|i| format!("p{i} = \"/Users/u/{i}/\""))
        .collect();
    let many = many.join("\n");
    let r = repo_with(&[("many.txt", &many), ("gone.txt", "/Users/me/x/\n")]).expect("fixture");
    std::fs::write(r.path().join("bin.dat"), b"\xff\xfe/Users/me/x/\n").expect("write");
    r.git(&["add", "bin.dat"]).expect("git");
    std::fs::remove_file(r.path().join("gone.txt")).expect("rm");
    let out = goh(Some(&r), &["home-paths"], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out.stdout.contains("in 205 location(s)"), "{}", out.stdout);
    assert!(out.stdout.contains("… and 5 more"), "{}", out.stdout);
    assert!(!out.stdout.contains("bin.dat"), "undecodable is skipped");
    assert!(
        !out.stdout.contains("gone.txt"),
        "a vanished file is skipped"
    );
}

// ------------------------------------------------------------------ no-allow

#[test]
fn no_allow_finds_outer_inner_and_wrapped_suppressions() {
    let wrapped = format!("#[{CFG_ATTR}(test, {}(unused))]\nfn c() {{}}\n", "expect");
    let r = repo_with(&[
        ("Cargo.toml", "[package]\nname = \"x\"\n"),
        (
            "src/a.rs",
            &format!("{}\nfn a() {{}}\n", attr("allow", "dead_code")),
        ),
        (
            "src/b.rs",
            &format!("#!{}\n", &attr("expect", "unused")[1..]),
        ),
        ("src/c.rs", &wrapped),
        ("tests/t.rs", &format!("{}\n", attr("allow", "x"))),
        (
            "tools/out_of_scope.rs",
            &format!("{}\n", attr("allow", "x")),
        ),
        ("src/upper.RS", &format!("{}\n", attr("allow", "x"))),
    ])
    .expect("fixture");
    let out = goh(Some(&r), &["no-allow"], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(
        out.stdout
            .contains("4 #[allow]/#[expect] in tracked Rust source"),
        "{}",
        out.stdout
    );
    for hit in ["src/a.rs:1:", "src/b.rs:1:", "src/c.rs:1:", "tests/t.rs:1:"] {
        assert!(out.stdout.contains(hit), "{hit} missing: {}", out.stdout);
    }
    assert!(!out.stdout.contains("tools/"), "{}", out.stdout);
    assert!(!out.stdout.contains("upper.RS"), "{}", out.stdout);
    assert!(out.stdout.contains("fix the finding properly"));
    let out = goh(Some(&r), &["no-allow", "--exclude", "^(src|tests)/"], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(
        out.stderr.contains("matched NO compiled source"),
        "{}",
        out.text()
    );
    let out = goh(Some(&r), &["no-allow", "--exclude", "("], &[]);
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
}

#[test]
fn no_allow_ignores_comments_strings_and_generated_files() {
    let prose = format!(
        "/// mentions {} in prose\n/* and {}\n   across lines */\nfn f() {{}}\n",
        attr("allow", "x"),
        attr("expect", "y")
    );
    let quoted = format!("#[{CFG_ATTR}(test, \"{}(y)\")]\nfn g() {{}}\n", "allow");
    let generated = format!("// @{}\n{}\n", "generated", attr("allow", "x"));
    let r = repo_with(&[
        ("Cargo.toml", "[workspace]\n"),
        ("src/prose.rs", &prose),
        ("src/quoted.rs", &quoted),
        ("src/gen.rs", &generated),
        ("build.rs", "fn main() {}\n"),
    ])
    .expect("fixture");
    let out = goh(Some(&r), &["no-allow"], &[]);
    assert!(out.status.success(), "{}", out.text());
    assert_eq!(out.stdout, "✓ [no_allow] OK — 4 files clean\n");
    // A repo without Rust is clean over zero files, not blind.
    let r = repo_with(&[("README.md", "x\n")]).expect("fixture");
    let out = goh(Some(&r), &["no-allow"], &[]);
    assert!(out.status.success(), "{}", out.text());
    assert!(out.stdout.contains("0 files clean"), "{}", out.text());
}

#[test]
fn no_allow_staged_reads_the_index_and_skips_vanished_files() {
    let r = repo_with(&[
        ("Cargo.toml", "[package]\n"),
        ("src/lib.rs", "pub fn f() {}\n"),
        ("src/gone.rs", "pub fn g() {}\n"),
    ])
    .expect("fixture");
    // Suppression only in the worktree: the index is clean.
    r.write(
        "src/lib.rs",
        &format!("{}\npub fn f() {{}}\n", attr("allow", "x")),
    )
    .expect("write");
    let out = goh(Some(&r), &["no-allow", "--staged"], &[]);
    assert!(out.status.success(), "{}", out.text());
    let out = goh(Some(&r), &["no-allow"], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    // Staged, it is what the commit holds: red, and named as staged.
    r.git(&["add", "src/lib.rs"]).expect("git");
    let out = goh(Some(&r), &["no-allow", "--staged"], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(
        out.stdout.contains("in staged Rust source"),
        "{}",
        out.text()
    );
    // A tracked file deleted from the worktree is counted, never read.
    r.write("src/lib.rs", "pub fn f() {}\n").expect("write");
    std::fs::remove_file(r.path().join("src/gone.rs")).expect("rm");
    let out = goh(Some(&r), &["no-allow"], &[]);
    assert!(out.status.success(), "{}", out.text());
    assert!(out.stdout.contains("2 files clean"), "{}", out.text());
}

// -------------------------------------------------------------------- screen

#[test]
fn screen_flags_live_display_calls_and_honours_its_exemptions() {
    let r = repo_with(&[
        ("tests/live.swift", "let s = NSScreen.main\n"),
        (
            "tests/typed.swift",
            "let s: NSScreen? = nil\nfunc f() -> [NSScreen] { [] }\n",
        ),
        (
            "tests/marked.swift",
            "// screen-ok: reads a fake\nlet s = NSScreen.main\n",
        ),
        ("tests/objc.m", "[w makeKeyAndOrderFront:nil];\n"),
        ("tests/drive.py", "import pyautogui\n"),
        (
            "tests/headless.py",
            "env[\"GOH_HEADLESS\"] = \"1\"\nsubprocess.run([\"screencapture\"])\n",
        ),
        ("tests/prose.py", "# screencapture is banned here\n"),
        ("tests/notes.txt", "NSScreen.main\n"),
        ("src/app.swift", "NSScreen.main\n"),
    ])
    .expect("fixture");
    std::fs::write(r.path().join("tests/blob.py"), b"import pyautogui\n\0\0").expect("write");
    r.git(&["add", "-A"]).expect("git");
    let out = goh(Some(&r), &["screen", "--scope", "tests/*"], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(
        out.stderr.contains("3 presentation call(s)"),
        "{}",
        out.stderr
    );
    assert!(out
        .stderr
        .contains("tests/live.swift:1: reads the real display's geometry"));
    assert!(out
        .stderr
        .contains("tests/objc.m:1: puts a window on the user's display"));
    assert!(out.stderr.contains("tests/drive.py:1: pyautogui drives"));
    for clean in [
        "tests/typed",
        "tests/marked",
        "tests/headless",
        "tests/prose",
        "tests/notes",
        "tests/blob",
        "src/app",
    ] {
        assert!(!out.stderr.contains(clean), "{clean}: {}", out.stderr);
    }
    assert!(out.stderr.contains("screen-ok:"), "the fix is named");
    // Explicit clean targets pass, counting only scannable languages.
    let out = goh(
        Some(&r),
        &[
            "screen",
            "tests/typed.swift",
            "tests/notes.txt",
            "tests/missing.py",
        ],
        &[],
    );
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stdout.contains(
            "→ [no_screen] OK — 1 test target(s) scanned, nothing renders to the display"
        ),
        "{}",
        out.text()
    );
}

#[test]
fn screen_walks_directories_without_caches_or_symlinked_dirs() {
    let r = repo_with(&[
        ("t/a/ok.swift", "let x = 1\n"),
        ("t/a/__pycache__/bad.py", "import pyautogui\n"),
        ("elsewhere/bad.swift", "NSScreen.main\n"),
    ])
    .expect("fixture");
    std::os::unix::fs::symlink(r.path().join("elsewhere"), r.path().join("t/link"))
        .expect("symlink");
    let abs = r.path().join("t").to_string_lossy().into_owned();
    let out = goh(Some(&r), &["screen", &abs], &[]);
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stdout.contains("1 test target(s) scanned"),
        "{}",
        out.text()
    );
    let out = goh(Some(&r), &["screen", "t", "elsewhere"], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(
        out.stderr.contains("elsewhere/bad.swift:1:"),
        "{}",
        out.text()
    );
}

#[test]
fn screen_scope_errors_and_the_staged_index() {
    let r = repo_with(&[("tests/a.swift", "NSScreen.main\n")]).expect("fixture");
    let out = goh(Some(&r), &["screen"], &[]);
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
    assert!(out.stderr.contains("no targets"), "{}", out.text());
    let out = goh(Some(&r), &["screen", "--scope", "nomatch/*"], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out.stderr.contains("--scope matched no tracked files"));
    let out = goh(Some(&r), &["screen", "--scope", "tests/[z-a]*"], &[]);
    assert_eq!(out.status.code(), Some(2), "{}", out.text());
    assert!(out.stderr.contains("bad --scope glob"), "{}", out.text());
    // A class and a negated class translate like fnmatch.
    let out = goh(Some(&r), &["screen", "--scope", "tests/[!b]*.swift"], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    let out = goh(Some(&r), &["screen", "--scope", "tests/[a"], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(
        out.stderr.contains("matched no tracked files"),
        "{}",
        out.text()
    );
    // Fix the worktree only: --staged still reads the violation.
    r.write("tests/a.swift", "let x = 1\n").expect("write");
    let out = goh(Some(&r), &["screen", "--staged", "--scope", "tests/*"], &[]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    let out = goh(Some(&r), &["screen", "--scope", "tests/*"], &[]);
    assert!(out.status.success(), "{}", out.text());
    r.git(&["add", "-A"]).expect("git");
    let out = goh(Some(&r), &["screen", "--staged", "--scope", "tests/*"], &[]);
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stdout.contains("1 test target(s) staged"),
        "{}",
        out.text()
    );
}

// ----------------------------------------------------------- shared failures

#[test]
fn a_broken_index_is_an_error_not_an_empty_tree() {
    let r =
        repo_with(&[("Cargo.toml", "[package]\n"), ("src/a.rs", "fn a() {}\n")]).expect("fixture");
    let bad = r.path().join("broken-index");
    std::fs::write(&bad, "not an index\n").expect("write");
    let env = [("GIT_INDEX_FILE", bad.to_str().expect("utf-8 path"))];
    for args in [
        vec!["no-allow"],
        vec!["home-paths"],
        vec!["home-paths", "--staged"],
        vec!["ceiling", "--max", "5"],
        vec!["screen", "--scope", "src/*"],
        vec!["markers"],
        vec!["length", "--max", "5"],
        vec!["emoji"],
        vec!["secrets"],
        vec!["structural", "--full"],
    ] {
        let out = goh(Some(&r), &args, &env);
        assert_eq!(out.status.code(), Some(2), "{args:?}: {}", out.text());
        assert!(out.stderr.contains("failed"), "{args:?}: {}", out.text());
    }
}

#[test]
fn outside_a_git_repo_every_newer_command_says_so_by_name() {
    let r = Repo::bare(tempfile::tempdir().expect("tempdir"));
    for (args, name) in [
        (vec!["home-paths"], "[no_home_paths]"),
        (vec!["no-allow"], "[no_allow]"),
        (vec!["screen", "--scope", "tests/*"], "[no_screen]"),
        (vec!["lints"], "[lints_optin]"),
        (vec!["ceiling", "--max", "5"], "[ceiling]"),
    ] {
        let out = goh(Some(&r), &args, &[]);
        assert!(out.status.success(), "{args:?}: {}", out.text());
        assert!(
            out.text().contains(name) && out.text().contains("not a git repo"),
            "{args:?} did not name the skip: {}",
            out.text()
        );
    }
}
