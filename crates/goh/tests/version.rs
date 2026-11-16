//! `goh --version` names the build's version, which is the TAG (`lib/goh_version.sh`,
//! GOH.SHIPRELREUSE): `X.Y.Z` on a release commit, `X.Y.Z-N-g<sha>` past one, never a number from
//! Cargo.toml, whose version is a placeholder a release does not touch. The install and the gate
//! compare it with the same script's answer, so a binary that cannot say what it is cannot be
//! verified as the one just built.
use std::process::Command;

#[test]
fn version_flag_reports_the_tag_derived_version() {
    let out = Command::new(env!("CARGO_BIN_EXE_goh"))
        .arg("--version")
        .output()
        .expect("goh runs");
    assert!(out.status.success());
    let text = String::from_utf8_lossy(&out.stdout);
    assert_eq!(text.trim(), format!("goh {}", env!("GOH_VERSION")));
    assert_ne!(
        env!("GOH_VERSION"),
        env!("CARGO_PKG_VERSION"),
        "the version is the tag's, not Cargo.toml's placeholder"
    );
}
