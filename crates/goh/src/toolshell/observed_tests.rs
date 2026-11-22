//! GOH.SHELLCHECKREAL: the check and the guard judge the shell the harness really runs, read from
//! a fixture snapshot directory or a tool-shell ancestor, never only from `CLAUDE_CODE_SHELL`.

use std::collections::BTreeMap;
use std::path::Path;
use std::time::{Duration, SystemTime};

use super::check::{check, Inputs, Probed};
use super::observed::{ancestors_from, newest_snapshot, no_ancestors, seen, tool_shell_of, Seen};
use super::{judge_event, uses_pipestatus};

const BASH: &str = "/opt/homebrew/bin/bash";

/// A config dir holding one snapshot per `(shell, age in seconds)`.
fn snapshots(of: &[(&str, u64)]) -> tempfile::TempDir {
    let dir = tempfile::tempdir().expect("tmp");
    let snaps = dir.path().join("shell-snapshots");
    std::fs::create_dir(&snaps).expect("mkdir");
    for (i, (shell, age)) in of.iter().enumerate() {
        let path = snaps.join(format!("snapshot-{shell}-17952828934{i}-abc.sh"));
        let file = std::fs::File::create(&path).expect("snapshot");
        file.set_modified(SystemTime::now() - Duration::from_secs(*age))
            .expect("mtime");
    }
    dir
}

fn env_for(config: &Path) -> impl Fn(&str) -> Option<String> {
    let map: BTreeMap<&str, String> = [
        ("HOME", "/h".to_owned()),
        ("SHELL", "/bin/zsh".to_owned()),
        ("CLAUDE_CODE_SHELL", BASH.to_owned()),
        ("BASH_ENV", "/h/.bashrc".to_owned()),
        (
            "CLAUDE_CONFIG_DIR",
            config.to_str().expect("utf8").to_owned(),
        ),
    ]
    .into_iter()
    .collect();
    move |k| map.get(k).cloned()
}

fn bash5() -> Probed {
    Probed {
        bash_major: Some(5),
        bash_version: "5.3.0(1)-release".to_owned(),
        goh_dir: "/g".to_owned(),
        pythonpath: "/g".to_owned(),
        agent_env_loaded: "1".to_owned(),
    }
}

fn shell_check(
    config: &Path,
    ancestors: &dyn Fn() -> Vec<(u32, String)>,
) -> Result<String, Vec<String>> {
    let env = env_for(config);
    check(&Inputs {
        env: &env,
        is_file: &|p: &Path| p == Path::new("/h/.bashrc"),
        probe: &|_: &str, _: Option<&str>, _: &str| Ok(bash5()),
        ancestors,
    })
}

#[test]
fn shell_check_is_red_in_a_zsh_tool_shell_though_the_setting_names_bash() {
    let config = snapshots(&[("bash", 600), ("zsh", 5)]);
    let refused = shell_check(config.path(), &no_ancestors)
        .expect_err("red")
        .join("\n");
    for needle in [
        "runs tool commands in zsh",
        "CLAUDE_CODE_SHELL=",
        "snapshot-zsh-",
        "pipestatus",
        "claude CLI",
    ] {
        assert!(refused.contains(needle), "no {needle:?} in {refused}");
    }
}

#[test]
fn shell_check_is_green_in_a_bash_tool_shell_and_names_its_witness() {
    let config = snapshots(&[("zsh", 600), ("bash", 5)]);
    let line = shell_check(config.path(), &no_ancestors).expect("green");
    assert!(
        line.contains("bash 5.3") && line.contains("snapshot-bash-"),
        "{line}"
    );
    let none = tempfile::tempdir().expect("tmp");
    let unseen = shell_check(none.path(), &no_ancestors).expect("green on the setting alone");
    assert!(unseen.contains("not seen running"), "{unseen}");
}

#[test]
fn a_tool_shell_ancestor_outranks_the_snapshots() {
    let config = snapshots(&[("bash", 5)]);
    let snaps = config.path().join("shell-snapshots");
    let zsh_line = format!(
        "/bin/zsh -c source {}/snapshot-zsh-1-x.sh && eval x",
        snaps.display()
    );
    let zsh_call = || vec![(900, "goh shell-check".to_owned()), (800, zsh_line.clone())];
    let refused = shell_check(config.path(), &zsh_call)
        .expect_err("red")
        .join("\n");
    assert!(
        refused.contains("in /bin/zsh") && refused.contains("pid 800"),
        "{refused}"
    );
    // A tool shell of ANOTHER config (the suite run inside a real tool call, its child given its
    // own HOME) is not this config's harness: the snapshots decide.
    let elsewhere = || {
        vec![(
            800,
            "/bin/zsh -c source /u/.claude/shell-snapshots/snapshot-zsh-1.sh".to_owned(),
        )]
    };
    let green = shell_check(config.path(), &elsewhere).expect("green");
    assert!(green.contains("snapshot-bash-"), "{green}");
    let zsh_snaps = snapshots(&[("zsh", 5)]);
    let bash_line = format!(
        "{BASH} -c source {}/snapshot-bash-1.sh",
        zsh_snaps.path().join("shell-snapshots").display()
    );
    let bash_call = || vec![(7, bash_line.clone())];
    let green = shell_check(zsh_snaps.path(), &bash_call).expect("green");
    assert!(green.contains("pid 7"), "{green}");
}

#[test]
fn the_guard_refuses_pipestatus_under_a_seen_zsh_and_passes_it_under_bash() {
    let command = "make test | tail -5; exit ${PIPESTATUS[0]}";
    let event = serde_json::json!({"tool_name": "Bash", "tool_input": {"command": command}});
    let zsh = snapshots(&[("zsh", 5)]);
    let reason = judge_event(&event, &env_for(zsh.path())).expect("refused under zsh");
    for needle in ["PIPESTATUS", "pipestatus[1]", "rc=$?", "# zsh-split-ok:"] {
        assert!(reason.contains(needle), "no {needle:?} in {reason}");
    }
    let bash = snapshots(&[("bash", 5)]);
    assert_eq!(judge_event(&event, &env_for(bash.path())), None);
    let escaped = serde_json::json!({"tool_name": "Monitor", "tool_input": {
        "command": format!("{command} # zsh-split-ok: runs under bash -c")}});
    assert_eq!(judge_event(&escaped, &env_for(zsh.path())), None);
    // The split finding, too, follows the seen shell rather than the bash setting.
    let split = serde_json::json!({"tool_name": "Bash", "tool_input": {"command": r#"F="a b"; git add $F"#}});
    assert!(judge_event(&split, &env_for(zsh.path())).is_some());
}

#[test]
fn pipestatus_is_found_only_where_it_expands() {
    for used in [
        "exit $PIPESTATUS",
        "echo ${PIPESTATUS[0]}",
        "echo ${#PIPESTATUS[@]}",
        "rc=(\"${PIPESTATUS[@]}\")",
    ] {
        assert!(uses_pipestatus(used), "{used}");
    }
    for unused in [
        "echo PIPESTATUS",
        "echo ${pipestatus[1]}",
        "echo $PIPESTATUSX",
        "git log --grep PIPESTATUS",
    ] {
        assert!(!uses_pipestatus(unused), "{unused}");
    }
}

#[test]
fn the_ancestor_chain_and_snapshot_names_are_read() {
    let table = "  1     0 /sbin/launchd\n 50     1 /Applications/Claude\n 60    50 /bin/zsh -c source /x/shell-snapshots/snapshot-zsh-1.sh\n 70    60 goh shell-check\n 99    98 orphan\n";
    let chain = ancestors_from(table, 70);
    let pids: Vec<u32> = chain.iter().map(|(p, _)| *p).collect();
    assert_eq!(pids, vec![70, 60, 50, 1]);
    let x = Path::new("/x/shell-snapshots");
    assert_eq!(tool_shell_of(&chain[1].1, x).as_deref(), Some("/bin/zsh"));
    assert_eq!(
        tool_shell_of(&chain[1].1, Path::new("/y/shell-snapshots")),
        None
    );
    assert_eq!(tool_shell_of("/bin/zsh -lc 'echo hi'", x), None);
    assert_eq!(
        tool_shell_of("vim /x/shell-snapshots/snapshot-zsh-1.sh", x),
        None
    );
    assert_eq!(ancestors_from("", 70), Vec::new());
    let config = snapshots(&[("bash", 50), ("zsh", 10), ("bash", 900)]);
    let (shell, path) = newest_snapshot(&config.path().join("shell-snapshots")).expect("one");
    assert_eq!(shell, "zsh");
    assert!(path.to_string_lossy().contains("snapshot-zsh-"));
    assert_eq!(newest_snapshot(Path::new("/nonexistent/dir")), None);
    let env = |k: &str| (k == "HOME").then(|| "/nonexistent-home".to_owned());
    assert_eq!(seen(&env, &no_ancestors), None);
    let got: Option<Seen> = seen(&env_for(config.path()), &no_ancestors);
    assert_eq!(got.map(|s| s.shell).as_deref(), Some("zsh"));
}

#[test]
fn the_real_ancestors_start_at_this_process_parent() {
    let chain = super::observed::real_ancestors();
    assert_eq!(
        chain.first().map(|(pid, _)| *pid),
        Some(std::os::unix::process::parent_id()),
        "{chain:?}"
    );
}
