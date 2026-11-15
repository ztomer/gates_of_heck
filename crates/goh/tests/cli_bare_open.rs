//! `goh bare-open` end to end (GOH.BAREOPEN2): each launch shape the first scan walked past is
//! red on a fixture repo, naming its line, and the clean spelling of each is green. Seeds are read
//! from an empty private store, so the machine's own store never changes the verdict.

use goh_testkit::{goh_at, repo_with, Out, Repo};

fn bare_open(repo: &Repo, store: &std::path::Path) -> Result<Out, String> {
    let store = store.to_str().ok_or("the store path is not UTF-8")?;
    goh_at(
        std::path::Path::new(env!("CARGO_BIN_EXE_goh")),
        Some(repo),
        &["bare-open"],
        &[("GOH_SEED_DIR", store)],
    )
    .map_err(|e| e.to_string())
}

#[test]
fn every_launch_shape_is_red_and_its_clean_spelling_is_green() {
    let store = tempfile::tempdir().expect("store");
    let r = repo_with(&[
        ("README.md", "x\n"),
        (
            "env_prefix.sh",
            "#!/bin/sh\nenv FOO=1 open -a \"$APP\"\n",
        ),
        (
            "subst.sh",
            "#!/bin/sh\nout=\"$(open -a \"$APP\")\"\n",
        ),
        (
            "launch.py",
            "import subprocess\nsubprocess.run([\"open\", \"-a\", APP], stdin=subprocess.DEVNULL)\n",
        ),
        (
            "launch_str.py",
            "import os\nos.system(\"open -a Foo\")\n",
        ),
    ])
    .expect("fixture");
    r.git(&["add", "-A"]).expect("git");
    let out = bare_open(&r, store.path()).expect("goh runs");
    assert!(!out.status.success(), "{}", out.text());
    for site in [
        "env_prefix.sh:2:",
        "subst.sh:2:",
        "launch.py:2:",
        "launch_str.py:2:",
    ] {
        assert!(out.text().contains(site), "{site} missing: {}", out.text());
    }
    // The clean spellings: `env -i`, goh's own launcher, a message that only mentions `open`.
    r.write(
        "env_prefix.sh",
        "#!/bin/sh\nenv -i /usr/bin/open -a \"$APP\"\n",
    )
    .expect("write");
    r.write(
        "subst.sh",
        "#!/bin/sh\nout=\"$(\\\"$GOH_DIR/bin/goh\\\" launch-app \\\"$APP\\\")\"\n",
    )
    .expect("write");
    r.write(
        "launch.py",
        "import subprocess\nsubprocess.run([GOH, \"launch-app\", APP], stdin=subprocess.DEVNULL)\n",
    )
    .expect("write");
    r.write("launch_str.py", "print(\"run open -a Foo by hand\")\n")
        .expect("write");
    r.git(&["add", "-A"]).expect("git");
    let out = bare_open(&r, store.path()).expect("goh runs");
    assert!(out.status.success(), "{}", out.text());
}
