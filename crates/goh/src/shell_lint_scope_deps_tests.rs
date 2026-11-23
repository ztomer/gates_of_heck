//! `Deps`, the inputs of a shell file's verdict key (`crates/goh/src/shell_lint_scope.rs`).

/// GOH.SCLINTKEY: a `.gatesrc` a shell file only MENTIONS (a message, a comment) is no input of
/// its verdict -- shellcheck reads a non-shell file only through a source statement -- so an
/// edit to it keeps the verdict of every file that does not source it, and misses for each
/// file whose source chain reaches it. A shell file stays an edge by mention (a dynamic path).
#[test]
fn a_non_shell_file_only_mentioned_is_no_input_of_the_verdict() {
    let base = std::env::temp_dir().join(format!("goh-sclintkey-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    assert!(std::fs::create_dir_all(&base).is_ok());
    let write = |f: &str, t: &str| assert!(std::fs::write(base.join(f), t).is_ok());
    write("a.sh", "echo 'set it in .gatesrc'\n");
    write("b.sh", ". ./.gatesrc\n");
    write("c.sh", "# runs b.sh\n. ./b.sh\n");
    write(".gatesrc", "GOH_X=1\n");
    let tracked: Vec<String> = ["a.sh", "b.sh", "c.sh", ".gatesrc"]
        .map(String::from)
        .to_vec();
    let shells: Vec<&String> = tracked[..3].iter().collect();
    let key = |f: &str| {
        super::Deps::build(&base, &tracked, &shells)
            .key(f)
            .unwrap_or_default()
    };
    let (a, b, c) = (key("a.sh"), key("b.sh"), key("c.sh"));
    assert!(!a.contains(".gatesrc"), "{a}");
    assert!(
        b.contains(".gatesrc=") && c.contains(".gatesrc="),
        "{b} | {c}"
    );
    write(".gatesrc", "GOH_X=1\nGOH_SELF_WALK=on\n");
    assert_eq!(key("a.sh"), a, "a mention made the .gatesrc an input");
    assert_ne!(key("b.sh"), b, "a sourced .gatesrc edit kept the verdict");
    assert_ne!(
        key("c.sh"),
        c,
        "a .gatesrc sourced through b.sh kept the verdict"
    );
    let _ = std::fs::remove_dir_all(&base);
}
