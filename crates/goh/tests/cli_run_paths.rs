//! `goh run-paths` end to end (GOH.CLASS.CHECKOUT part 2): each shape the class was found in is red
//! on a fixture repo, naming its line; the helper's spelling is green; and the seed is a ratchet --
//! an entry covers its count and no more, and an entry whose file is clean is stale and red. Seeds
//! are read from an empty private store, so the machine's own store never changes the verdict.

use goh_testkit::{git_command, goh_at, repo_with, Out, Repo};

fn run_paths(repo: &Repo, store: &std::path::Path) -> Result<Out, String> {
    let store = store.to_str().ok_or("the store path is not UTF-8")?;
    goh_at(
        std::path::Path::new(env!("CARGO_BIN_EXE_goh")),
        Some(repo),
        &["run-paths"],
        &[("GOH_SEED_DIR", store)],
    )
    .map_err(|e| e.to_string())
}

fn root_commit(r: &Repo) -> Result<String, String> {
    let out = git_command()
        .args(["rev-list", "--max-parents=0", "HEAD"])
        .current_dir(r.path())
        .stdin(std::process::Stdio::null())
        .output()
        .map_err(|e| e.to_string())?;
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_owned())
}

fn seed(r: &Repo, path: &str, count: usize) -> Result<(), String> {
    let body = format!(
        "{{\"why\": \"t\", \"entries\": [{{\"root\": \"{}\", \"repo\": \"t\", \"path\": \"{path}\", \
         \"count\": {count}, \"reason\": \"GOH.CLASS.CHECKOUT fixture\"}}]}}\n",
        root_commit(r)?
    );
    r.write("run_paths_seed.json", &body)
        .map_err(|e| e.to_string())?;
    r.git(&["add", "-A"])
}

#[test]
fn each_shape_is_red_and_the_helpers_spelling_is_green() {
    let store = tempfile::tempdir().expect("store");
    let r = repo_with(&[
        ("README.md", "x\n"),
        (
            "build.sh",
            "#!/bin/bash\nLOCK=\"${PROJECT_ROOT}/bin/.build.lock\"\n",
        ),
        ("place.sh", "#!/bin/bash\nstage=\"$2.$$.staging\"\n"),
        ("rel.sh", "#!/bin/bash\nwt=\"$main/build/release-$TAG\"\n"),
        ("tree.sh", "#!/bin/bash\ntree=\"$(git write-tree)\"\n"),
        (
            "lock.py",
            "import tempfile\nfd, tmp = tempfile.mkstemp(dir=repo)\n",
        ),
        ("key.rs", "fn t() { let t = git(&[\"write-tree\"]); }\n"),
    ])
    .expect("fixture");
    let out = run_paths(&r, store.path()).expect("goh runs");
    assert!(!out.status.success(), "{}", out.text());
    for site in [
        "build.sh:2:",
        "place.sh:2:",
        "rel.sh:2:",
        "tree.sh:2:",
        "lock.py:2:",
        "key.rs:1:",
    ] {
        assert!(out.text().contains(site), "{site} missing: {}", out.text());
    }
    r.write(
        "build.sh",
        "#!/bin/bash\nLOCK=\"$(goh_run_dir \"$PROJECT_ROOT\")/build.lock\"\n",
    )
    .expect("write");
    r.write(
        "place.sh",
        "#!/bin/bash\nstage=\"$2.$$.staging\"  # run-path-ok: another filesystem\n",
    )
    .expect("write");
    r.write(
        "rel.sh",
        "#!/bin/bash\nwt=\"$(goh_run_dir \"$main\")/release\"\n",
    )
    .expect("write");
    r.write("tree.sh", "#!/bin/bash\ntree=\"$(goh_index_tree)\"\n")
        .expect("write");
    r.write(
        "lock.py",
        "import tempfile\nfd, tmp = tempfile.mkstemp(prefix='goh-x.')\n",
    )
    .expect("write");
    r.write(
        "key.rs",
        "fn t() { let t = crate::run_path::index_tree(None); }\n",
    )
    .expect("write");
    r.git(&["add", "-A"]).expect("git");
    let out = run_paths(&r, store.path()).expect("goh runs");
    assert!(out.status.success(), "{}", out.text());
}

#[test]
fn the_seed_covers_its_count_and_a_stale_entry_is_red() {
    let store = tempfile::tempdir().expect("store");
    let r = repo_with(&[
        ("README.md", "x\n"),
        ("a.sh", "#!/bin/bash\nl=\"$repo/.a.lock\"\n"),
    ])
    .expect("fixture");
    r.git(&[
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-qm",
        "x",
    ])
    .expect("commit");
    seed(&r, "a.sh", 1).expect("seed");
    let out = run_paths(&r, store.path()).expect("goh runs");
    assert!(
        out.status.success(),
        "seeded at its count warns: {}",
        out.text()
    );
    // One entry does not launder a second site in the same file.
    r.write(
        "a.sh",
        "#!/bin/bash\nl=\"$repo/.a.lock\"\nm=\"$repo/.b.lock\"\n",
    )
    .expect("write");
    r.git(&["add", "-A"]).expect("git");
    let out = run_paths(&r, store.path()).expect("goh runs");
    assert!(
        !out.status.success(),
        "over its count is red: {}",
        out.text()
    );
    // Fixed, the entry stays behind: stale, and red until it is deleted.
    r.write(
        "a.sh",
        "#!/bin/bash\nl=\"$(goh_run_dir \"$repo\")/a.lock\"\n",
    )
    .expect("write");
    r.git(&["add", "-A"]).expect("git");
    let out = run_paths(&r, store.path()).expect("goh runs");
    assert!(
        !out.status.success(),
        "a stale entry is red: {}",
        out.text()
    );
    assert!(out.text().contains("a.sh"), "{}", out.text());
}
