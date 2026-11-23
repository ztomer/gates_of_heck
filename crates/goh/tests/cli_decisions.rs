//! `goh decisions` and `goh private-names --add` end to end: the binary's exit code and words on a
//! fixture repo (GOH.LEDGERGATE, GOH.PRIVLISTORDER).

use goh_testkit::{goh_at, repo_with, Out, Repo};

const REC: &str = "# D-0001 \u{2014} a\n\n- **Question:** blue?\n- **Evidence:** looked.\n\
- **Options priced:** two.\n- **Ruling:** blue.\n- **Falsifier:** grey.\n\
- **Re-open when:** night.\n- **Lives in:** `a.py`\n";

fn goh(repo: &Repo, args: &[&str], envs: &[(&str, &str)]) -> Result<Out, String> {
    goh_at(
        std::path::Path::new(env!("CARGO_BIN_EXE_goh")),
        Some(repo),
        args,
        envs,
    )
    .map_err(|e| e.to_string())
}

#[test]
fn decisions_is_green_on_a_whole_record_and_red_without_its_falsifier() -> Result<(), String> {
    let r = repo_with(&[("docs/decisions/D-0001-a.md", REC), ("a.py", "x = 1\n")])?;
    let out = goh(&r, &["decisions"], &[])?;
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.text().contains("1 record, seven fields each"),
        "{}",
        out.text()
    );
    r.write(
        "docs/decisions/D-0001-a.md",
        &REC.replace("- **Falsifier:** grey.\n", ""),
    )
    .map_err(|e| e.to_string())?;
    r.git(&["add", "-A"])?;
    let out = goh(&r, &["decisions", "--staged"], &[])?;
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(
        out.text().contains("`Falsifier` is missing or empty"),
        "{}",
        out.text()
    );
    Ok(())
}

#[test]
fn private_names_add_refuses_a_named_name_and_adds_a_clean_one() -> Result<(), String> {
    let r = repo_with(&[("a.rs", "// found in Acme\n")])?;
    let store = tempfile::tempdir().map_err(|e| e.to_string())?;
    let dir = store.path().to_str().ok_or("store path")?;
    let env = [("GOH_SEED_DIR", dir)];
    let out = goh(&r, &["private-names", "--add", "Acme"], &env)?;
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    assert!(out.text().contains("nothing added"), "{}", out.text());
    let out = goh(&r, &["private-names", "--add", "Other"], &env)?;
    assert!(out.status.success(), "{}", out.text());
    let list = std::fs::read_to_string(store.path().join("private_names.txt"))
        .map_err(|e| e.to_string())?;
    assert!(list.lines().any(|l| l == "Other"), "{list}");
    Ok(())
}
