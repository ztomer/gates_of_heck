use super::*;

const K: Kind = Kind {
    file: "x_seed.json",
    unit: "site",
    name: "x seed",
};

fn seed(entries: &[(&str, &str, usize)]) -> Seed {
    entries
        .iter()
        .map(|(r, p, n)| ((r.to_string(), p.to_string()), Held::tracked(*n)))
        .collect()
}

fn found(entries: &[(&str, usize)]) -> BTreeMap<String, usize> {
    entries.iter().map(|(p, n)| (p.to_string(), *n)).collect()
}

fn entry(root: &str, count: usize) -> String {
    format!(
        r#"{{"entries":[{{"root":"{root}","repo":"x","path":"a.sh","count":{count},"reason":"r"}}]}}"#
    )
}

/// A committed fixture repo and its root commit.
fn committed() -> (goh_testkit::Repo, String) {
    let r = goh_testkit::repo_with(&[("a.sh", "true\n")]).expect("fixture");
    r.git(&["commit", "-qm", "root"]).expect("commit");
    let root = crate::gitutil::root_commit(r.path()).expect("root commit");
    (r, root)
}

#[test]
fn unseeded_and_over_are_red_at_and_under_warn() {
    let s = seed(&[("r", "a.sh", 1), ("r", "b.sh", 2), ("r", "c.sh", 2)]);
    let v = judge(
        "r",
        &found(&[("a.sh", 1), ("b.sh", 3), ("c.sh", 1), ("d.sh", 1)]),
        &s,
        false,
        K,
    );
    assert_eq!(v.red, vec!["b.sh".to_string(), "d.sh".to_string()]);
    assert_eq!(v.warn.len(), 2, "{:?}", v.warn);
    assert!(
        v.warn[1].1.contains("lower its entry in x_seed.json"),
        "{:?}",
        v.warn
    );
}

#[test]
fn a_full_run_calls_a_stale_entry_red_and_another_repos_entry_is_not_this_ones() {
    let s = seed(&[("r", "gone.sh", 1), ("q", "a.sh", 1)]);
    let v = judge("r", &found(&[("a.sh", 1)]), &s, true, K);
    assert_eq!(v.red, vec!["a.sh".to_string()]);
    assert_eq!(v.warn, Vec::<(String, String)>::new());
    assert_eq!(v.stale.len(), 1);
    assert!(v.stale[0].1.contains("delete its entry from x_seed.json"));
    let staged = judge("r", &found(&[]), &s, false, K);
    assert_eq!(
        staged.stale,
        Vec::<(String, String)>::new(),
        "a staged run reads too little to call an entry stale"
    );
    let report = stale_report("[x]", "GOH.X", &v.stale);
    assert!(report.contains("1 stale GOH.X seed entry") && report.contains("gone.sh"));
    assert_eq!(stale_report("[x]", "GOH.X", &[]), "");
}

#[test]
fn an_entry_without_a_reason_is_refused() {
    let dir = tempfile::tempdir().expect("tmp");
    std::fs::write(
        dir.path().join(K.file),
        r#"{"entries":[{"root":"r","repo":"x","path":"a.sh","count":1,"reason":""}]}"#,
    )
    .expect("write");
    let e = load_from(dir.path(), None, K).expect_err("no reason is refused");
    assert!(
        e.contains("is not a x seed") && e.contains("has no reason"),
        "{e}"
    );
    let empty = tempfile::tempdir().expect("tmp");
    assert_eq!(
        load_from(empty.path(), Some(empty.path()), K).expect("missing is empty"),
        Seed::new()
    );
}

#[test]
fn a_tracked_entry_naming_another_repositorys_root_is_refused() {
    let (r, own) = committed();
    r.write(
        K.file,
        &entry("bdbe3ad698c8f8591d4aed721cb761a6fddac09d", 1),
    )
    .expect("write");
    let e = load_from(r.path(), None, K).expect_err("a foreign entry is refused");
    assert!(
        e.contains("another repository") && e.contains("bdbe3ad6") && e.contains(&own),
        "{e}"
    );
    r.write(K.file, &entry(&own, 1)).expect("write");
    let s = load_from(r.path(), None, K).expect("its own entry loads");
    assert_eq!(s.get(&(own, "a.sh".to_string())), Some(&Held::tracked(1)));
}

#[test]
fn the_store_holds_any_repositorys_entry_and_one_entry_in_both_homes_is_refused() {
    let (r, own) = committed();
    let store = tempfile::tempdir().expect("tmp");
    std::fs::write(store.path().join(K.file), entry(&own, 2)).expect("write");
    let s = load_from(r.path(), Some(store.path()), K).expect("the store loads");
    let held = s[&(own.clone(), "a.sh".to_string())];
    assert_eq!((held.count, held.stored), (2, true));
    r.write(K.file, &entry(&own, 2)).expect("write");
    let e = load_from(r.path(), Some(store.path()), K).expect_err("both homes is refused");
    assert!(e.contains("seeded in both"), "{e}");
}

#[test]
fn two_clones_of_one_repository_in_the_store_name_the_store_alone() {
    let (r, own) = committed();
    let store = tempfile::tempdir().expect("tmp");
    let one = format!(r#"{{"root":"{own}","repo":"v/a","path":"a.sh","count":1,"reason":"r"}}"#);
    let two = one.replace("v/a", "v/a-local");
    std::fs::write(
        store.path().join(K.file),
        format!(r#"{{"entries":[{one},{two}]}}"#),
    )
    .expect("write");
    let e = load_from(r.path(), Some(store.path()), K).expect_err("one key twice is refused");
    assert!(
        e.contains("seeded twice in") && !e.contains("in both"),
        "{e}"
    );
}

#[test]
fn a_shallow_clone_takes_its_own_files_entries_as_its_own() {
    let (r, own) = committed();
    r.write(K.file, &entry(&own, 1)).expect("write");
    r.git(&["add", "-A"]).expect("add");
    r.git(&["commit", "-qm", "seed"]).expect("commit");
    let dst = tempfile::tempdir().expect("tmp");
    let url = format!("file://{}", r.path().display());
    goh_testkit::git_in(
        dst.path(),
        &["clone", "-q", "--depth", "1", &url, "shallow"],
    )
    .expect("shallow clone");
    let clone = dst.path().join("shallow");
    assert!(crate::gitutil::is_shallow(&clone));
    let head = crate::gitutil::root_commit(&clone).expect("a root it can see");
    assert_ne!(head, own, "a shallow clone cannot see its true root");
    let s = load_from(&clone, None, K).expect("its own file is its own");
    assert!(s.contains_key(&(head, "a.sh".to_string())), "{s:?}");
}

#[test]
fn a_repository_with_no_commit_yet_takes_its_own_files_entries_as_its_own() {
    let r = goh_testkit::repo_with(&[("a.sh", "true\n")]).expect("fixture");
    r.write(
        K.file,
        &entry("73391f04f5f2b92ab9813c62e6cecdce919d2d86", 1),
    )
    .expect("write");
    let s = load_from(r.path(), None, K).expect("no root to compare: its own");
    assert!(
        s.contains_key(&(String::new(), "a.sh".to_string())),
        "{s:?}"
    );
}

const GOH_WORKFLOW: &str = "jobs:\n  gate:\n    steps:\n      - uses: actions/checkout@v6\n        with:\n          repository: ztomer/gates_of_heck\n          path: .goh\n";

/// A committed repo with `workflow` (if any) as its CI, and a store holding one entry for it.
fn store_ci_case(workflow: Option<&str>) -> (goh_testkit::Repo, tempfile::TempDir) {
    let (r, own) = committed();
    if let Some(w) = workflow {
        r.write(".github/workflows/ci.yml", w).expect("workflow");
    }
    let store = tempfile::tempdir().expect("tmp");
    std::fs::write(store.path().join(K.file), entry(&own, 1)).expect("write");
    (r, store)
}

#[test]
fn a_store_only_entry_for_a_repository_whose_ci_runs_goh_is_refused_naming_it() {
    let (r, store) = store_ci_case(Some(GOH_WORKFLOW));
    let e = load_at(r.path(), Some(store.path()), K, STORE_CI_REFUSED_FROM)
        .expect_err("a store entry its CI cannot read is refused");
    let name = r
        .path()
        .file_name()
        .expect("name")
        .to_string_lossy()
        .into_owned();
    assert!(
        e.contains(&name) && e.contains("runs goh in GitHub CI"),
        "{e}"
    );
    assert!(e.contains("ci.yml") && e.contains("GOH.STORECI"), "{e}");
    // before the day it is refused it warns, and the repository stays green
    let s = load_at(r.path(), Some(store.path()), K, STORE_CI_REFUSED_FROM - 1)
        .expect("a warning until the day");
    assert_eq!(s.len(), 1);
}

#[test]
fn a_store_entry_for_a_repository_without_goh_in_its_ci_is_accepted() {
    for workflow in [
        None,
        Some("jobs:\n  build:\n    steps:\n      - run: make\n"),
        // naming goh in a comment is not running it (servers/media_server's ci.yml does)
        Some("# refused by the gates_of_heck structural gate\n# repository: ztomer/gates_of_heck\njobs: {}\n"),
    ] {
        let (r, store) = store_ci_case(workflow);
        let s = load_at(r.path(), Some(store.path()), K, STORE_CI_REFUSED_FROM)
            .unwrap_or_else(|e| panic!("{workflow:?}: {e}"));
        assert_eq!(s.len(), 1, "{workflow:?}");
    }
}

#[test]
fn every_way_a_workflow_brings_goh_in_is_seen() {
    for line in [
        "          repository: ztomer/gates_of_heck",
        "          git clone --depth 1 https://github.com/ztomer/gates_of_heck \"$GOH_DIR\"",
        "        uses: ztomer/gates_of_heck/.github/actions/goh-floors@274ce50e # v0.54.1",
    ] {
        let r =
            goh_testkit::repo_with(&[(".github/workflows/w.yaml", &format!("jobs:\n{line}\n"))])
                .expect("fixture");
        assert_eq!(goh_workflow(r.path()).as_deref(), Some("w.yaml"), "{line}");
    }
}

#[test]
fn a_commit_settles_only_the_entries_of_what_it_read() {
    let mut s = seed(&[("r", "t0.sh", 1), ("r", "t1.sh", 2), ("q", "a.sh", 1)]);
    for (p, n) in [("a.sh", 2), ("b.sh", 3), ("c.sh", 1)] {
        s.insert(
            ("r".to_string(), p.to_string()),
            Held {
                count: n,
                stored: true,
            },
        );
    }
    let read: std::collections::BTreeSet<String> = ["a.sh", "b.sh", "t0.sh", "t1.sh"]
        .into_iter()
        .map(str::to_string)
        .collect();
    let out = settle("r", &found(&[("b.sh", 1), ("t1.sh", 1)]), &s, &read, K);
    // a.sh is read and clean (deleted), b.sh lowered, c.sh unread and untouched; another repo's
    // a.sh is not this one's.
    assert_eq!(out.store, found(&[("a.sh", 0), ("b.sh", 1)]));
    // A tracked entry left with nothing is stale at the commit; one only lowered is the push's
    // warning to lower, as before.
    assert_eq!(out.stale.len(), 1, "{:?}", out.stale);
    assert_eq!(out.stale[0].0, "t0.sh");
    assert!(out.stale[0].1.contains("in this commit"), "{:?}", out.stale);
    let now = settled_seed(&s, "r", &out);
    assert!(!now.contains_key(&("r".to_string(), "a.sh".to_string())));
    assert_eq!(now[&("r".to_string(), "b.sh".to_string())].count, 1);
    assert!(now.contains_key(&("q".to_string(), "a.sh".to_string())));
}

#[test]
fn a_settled_store_keeps_every_other_entry_and_its_shape() {
    let dir = tempfile::tempdir().expect("tmp");
    std::fs::write(
        dir.path().join(K.file),
        r#"{"why":"w","entries":[
          {"root":"r","repo":"x","path":"a.sh","count":2,"reason":"one"},
          {"root":"r","repo":"x","path":"b.sh","count":1,"reason":"two"},
          {"root":"q","repo":"y","path":"a.sh","count":1,"reason":"three"}]}"#,
    )
    .expect("write");
    let settled = Settle {
        store: found(&[("a.sh", 1), ("b.sh", 0)]),
        stale: Vec::new(),
    };
    write_settled(dir.path(), K, "r", &settled).expect("settled");
    let v: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(dir.path().join(K.file)).expect("read"))
            .expect("json");
    assert_eq!(v["why"], "w");
    let e = v["entries"].as_array().expect("entries");
    assert_eq!(e.len(), 2, "{e:?}");
    assert_eq!(
        (e[0]["path"].as_str(), e[0]["count"].as_u64()),
        (Some("a.sh"), Some(1))
    );
    assert!(e[0]["reason"]
        .as_str()
        .is_some_and(|r| r.starts_with("one;")));
    assert_eq!(e[1]["root"], "q");
    let left: Vec<_> = std::fs::read_dir(dir.path()).expect("dir").collect();
    assert_eq!(left.len(), 1, "no temporary file left behind");
}

#[test]
fn a_journalled_settle_writes_nothing_until_applied_and_merges_once() {
    let store = tempfile::tempdir().expect("store");
    let pending = tempfile::tempdir().expect("pending");
    let text = r#"{"entries":[{"root":"r","repo":"x","path":"a.sh","count":2,"reason":"one"}]}"#;
    std::fs::write(store.path().join(K.file), text).expect("write");
    let settled = Settle {
        store: found(&[("a.sh", 1)]),
        stale: Vec::new(),
    };
    let lowered = Settle {
        store: found(&[("a.sh", 0)]),
        stale: Vec::new(),
    };
    for s in [&settled, &lowered] {
        let said = pending::journal(pending.path(), store.path(), K, "r", s, "t", "I");
        assert!(said.contains("GOH.SEEDREFUSED"), "{said}");
    }
    let held = std::fs::read_to_string(store.path().join(K.file)).expect("read");
    assert_eq!(held, text, "a journal writes no store");
    let (code, said) = apply_pending(pending.path());
    assert_eq!(code, 0, "{said}");
    assert_eq!(
        said.matches("a.sh").count(),
        1,
        "one line per entry: {said}"
    );
    assert!(
        said.contains("entry deleted"),
        "the lowest count wins: {said}"
    );
    let v: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(store.path().join(K.file)).expect("read"))
            .expect("json");
    assert_eq!(v["entries"].as_array().map(Vec::len), Some(0));
    assert_eq!(std::fs::read_dir(pending.path()).expect("dir").count(), 0);
}
