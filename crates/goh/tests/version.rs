//! `goh --version` names the crate version: the install and release scripts
//! compare it against Cargo.toml, so a binary that cannot say what it is
//! cannot be verified as the one just built.
use std::process::Command;

#[test]
fn version_flag_reports_the_crate_version() {
    let out = Command::new(env!("CARGO_BIN_EXE_goh"))
        .arg("--version")
        .output()
        .expect("goh runs");
    assert!(out.status.success());
    let text = String::from_utf8_lossy(&out.stdout);
    assert_eq!(text.trim(), format!("goh {}", env!("CARGO_PKG_VERSION")));
}
