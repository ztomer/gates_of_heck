//! A house script killed by a signal exits 128+N through the wrapper (GOH.WRAPSIGNAL).
//!
//! `goh land --wait` answers 2 for "no request"; the wrapper used to map a script's signal death
//! (no exit code) to 2 as well, so a killed wait read as a missing land. The fixture root holds a
//! `tools/landq.py` that is really a shell script and `GOH_PY` names `sh`, so no Python runs.

// Test-only crate: helpers `expect` fixture writes, and a fixture that
// cannot be built is a broken test, not a result.
#![cfg(test)]

use goh_testkit::{goh_at, repo_with, Out};

/// `goh land ARGS` where `tools/landq.py` is the shell `body`.
fn land(body: &str, wait_for: &str) -> Out {
    let root = repo_with(&[("tools/landq.py", body)]).expect("fixture");
    let dir = root.path().to_str().expect("utf-8 path").to_owned();
    goh_at(
        std::path::Path::new(env!("CARGO_BIN_EXE_goh")),
        None,
        &["land", "--wait", wait_for],
        &[
            ("GOH_DIR", &dir),
            ("GOH_PY", "/bin/sh"),
            ("GOH_PY_OK", "/bin/sh"),
        ],
    )
    .expect("goh runs")
}

#[test]
fn a_script_killed_by_sigterm_exits_143() {
    let out = land("kill -TERM $$\n", "X");
    assert_eq!(out.status.code(), Some(143), "{}", out.text());
}

#[test]
fn a_script_killed_by_sigkill_exits_137() {
    let out = land("kill -KILL $$\n", "X");
    assert_eq!(out.status.code(), Some(137), "{}", out.text());
}

#[test]
fn a_script_exit_code_still_passes_through() {
    for code in [0, 2, 3, 7] {
        let out = land(&format!("exit {code}\n"), "X");
        assert_eq!(out.status.code(), Some(code), "{}", out.text());
    }
}
