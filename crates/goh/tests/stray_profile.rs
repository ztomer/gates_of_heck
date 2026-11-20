//! An instrumented `goh` never writes its profile into the directory it was started in
//! (GOH.STRAYPROFRAW). Started with no `LLVM_PROFILE_FILE`, the LLVM runtime's default is the
//! relative `default_%m_%p.profraw`: on 2026-11-20 two of them (383.6K, the coverage build's
//! module signature) appeared untracked in the loop's checkout, left by an agent that ran the
//! ship's instrumented binary (`suite-wt/target/llvm-cov-external/debug/goh --version`) from there.
//! The build now names a default under the temp directory (`goh_sys::name_profile`), so a run
//! nobody pointed anywhere still leaves nothing in its cwd.
//!
//! Meaningful in the coverage build (`cfg(coverage)`: this test's binary is instrumented there);
//! a plain build writes no profile at all, so the cwd half holds trivially.
use std::path::Path;
use std::process::Command;

fn profiles(dir: &Path) -> Vec<String> {
    let mut found = Vec::new();
    let mut stack = vec![dir.to_path_buf()];
    while let Some(d) = stack.pop() {
        for e in std::fs::read_dir(&d).into_iter().flatten().flatten() {
            let p = e.path();
            if p.is_dir() {
                stack.push(p);
            } else if p.extension().is_some_and(|x| x == "profraw") {
                found.push(p.display().to_string());
            }
        }
    }
    found
}

#[test]
fn a_run_with_no_profile_named_writes_none_into_its_cwd() {
    let cwd = tempfile::tempdir().expect("a scratch cwd");
    let tmp = tempfile::tempdir().expect("a scratch TMPDIR");
    let out = Command::new(env!("CARGO_BIN_EXE_goh"))
        .arg("--version")
        .current_dir(cwd.path())
        .env_remove("LLVM_PROFILE_FILE")
        .env("TMPDIR", tmp.path())
        .output()
        .expect("goh runs");
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert_eq!(
        profiles(cwd.path()),
        Vec::<String>::new(),
        "a profile left in the cwd"
    );
    // The profile is still written, where the build named it: the run is counted nowhere, and lost
    // nowhere a person would trip over it.
    if cfg!(coverage) {
        assert_eq!(
            profiles(tmp.path()).len(),
            1,
            "the named default under TMPDIR"
        );
    }
}

#[test]
fn a_named_profile_still_wins() {
    let cwd = tempfile::tempdir().expect("a scratch cwd");
    let named = tempfile::tempdir().expect("a profile directory");
    let out = Command::new(env!("CARGO_BIN_EXE_goh"))
        .arg("--version")
        .current_dir(cwd.path())
        .env("LLVM_PROFILE_FILE", named.path().join("p-%p.profraw"))
        .output()
        .expect("goh runs");
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert_eq!(profiles(cwd.path()), Vec::<String>::new());
    if cfg!(coverage) {
        assert_eq!(
            profiles(named.path()).len(),
            1,
            "LLVM_PROFILE_FILE names the profile"
        );
    }
}
