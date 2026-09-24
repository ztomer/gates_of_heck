//! The opt-in kill-by-name step end to end: it runs only when `.gatesrc`
//! declares it, stops the pipeline red naming the line, and passes once the
//! kill is scoped to what the caller owns. Split from `cli.rs` at the line
//! cap. The command name is assembled from parts because the gate reads this
//! file too.

use goh_testkit::{goh_at, repo_with, Out, Repo};

const PKILL: &str = concat!("p", "kill");

fn goh(repo: &Repo) -> std::io::Result<Out> {
    goh_at(
        std::path::Path::new(env!("CARGO_BIN_EXE_goh")),
        Some(repo),
        &["structural", "--full"],
        &[],
    )
}

#[test]
fn the_kill_by_name_gate_runs_only_when_declared_and_goes_red_on_a_pkill() {
    let kill = format!("import subprocess\nsubprocess.run([\"{PKILL}\", \"-f\", \"helper\"])\n");
    // Undeclared: the step does not run, and the kill passes untouched.
    let r = repo_with(&[
        ("README.md", "x\n"),
        ("run.py", &kill),
        (".gatesrc", "GOH_MAX_LINES=500\n"),
    ])
    .expect("fixture");
    r.git(&["add", "-A"]).expect("git");
    let out = goh(&r).expect("goh runs");
    assert!(out.status.success(), "{}", out.text());
    assert!(!out.text().contains("kill by name"), "{}", out.text());
    // Declared: it runs, names the line, and stops the pipeline red.
    r.write(".gatesrc", "GOH_MAX_LINES=500\nGOH_NO_KILL_BY_NAME=1\n")
        .expect("write");
    r.git(&["add", "-A"]).expect("git");
    let out = goh(&r).expect("goh runs");
    assert!(!out.status.success(), "{}", out.text());
    assert!(
        out.text().contains("no process kill by name"),
        "{}",
        out.text()
    );
    assert!(out.text().contains("run.py:2:"), "{}", out.text());
    // Scoped to the caller's own process group: green, and the step is listed.
    r.write("run.py", "import os\nos.killpg(os.getpgid(0), 15)\n")
        .expect("write");
    r.git(&["add", "-A"]).expect("git");
    let out = goh(&r).expect("goh runs");
    assert!(out.status.success(), "{}", out.text());
    assert!(
        out.stdout.contains("no process kill by name"),
        "{}",
        out.text()
    );
}
