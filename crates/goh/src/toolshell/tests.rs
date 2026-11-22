//! The tool-shell guard and check, both ways. The three refused commands are the ones that ran
//! wrong on 2026-11-21; each passes in its array or function form.

use std::collections::BTreeMap;
use std::path::Path;

use super::check::{check, parse_probe, probe_within, real_probe, Inputs, Probed};
use super::split::{findings, Role};
use super::{deny, escaped, guard_output, is_zsh, judge_event, tool_shell};

/// The three commands as they ran, then the role each finding takes.
const TODAY: [(&str, &str, Role); 3] = [
    (r#"F="a b"; git commit -- $F"#, "F", Role::Argument),
    (
        r#"L="env A=1 python x.py"; $L done X"#,
        "L",
        Role::CommandWord,
    ),
    (
        r#"FILES="a b"; for f in $FILES; do git checkout HEAD -- $f; done"#,
        "FILES",
        Role::ForList,
    ),
];

/// Their array, function and inline forms, and the neighbours that must pass.
const PASSING: [&str; 12] = [
    r#"F=(a b); git commit -- "${F[@]}""#,
    r#"L() { env A=1 python x.py "$@"; }; L done X"#,
    r#"FILES=(a b); for f in "${FILES[@]}"; do git checkout HEAD -- "$f"; done"#,
    "for f in a b; do git checkout HEAD -- \"$f\"; done",
    r#"F="a b"; git commit -m "$F""#,
    r"F=one; git commit -- $F",
    r#"F="a b" git commit -- $F"#,
    r#"F="a b"; F=one; echo $F"#,
    r#"F="a b"; F=(a b); echo $F"#,
    r"echo $UNSET_ANYWHERE",
    r#"F='a b'; echo "${F}""#,
    "if then fi ((( unparsable",
];

fn zsh_env(key: &str) -> Option<String> {
    (key == "SHELL").then(|| "/bin/zsh".to_owned())
}

fn bash_env(key: &str) -> Option<String> {
    match key {
        "SHELL" => Some("/bin/zsh".to_owned()),
        "CLAUDE_CODE_SHELL" => Some("/opt/homebrew/bin/bash".to_owned()),
        _ => None,
    }
}

fn event(command: &str) -> serde_json::Value {
    serde_json::json!({"tool_name": "Bash", "tool_input": {"command": command}})
}

#[test]
fn todays_three_commands_are_found_with_their_role() {
    for (command, var, role) in TODAY {
        let found = findings(command);
        assert_eq!(found.len(), 1, "{command}: {found:?}");
        assert_eq!(
            (found[0].var.as_str(), found[0].role),
            (var, role),
            "{command}"
        );
    }
}

#[test]
fn the_array_function_and_inline_forms_pass() {
    for command in PASSING {
        assert_eq!(findings(command), Vec::new(), "{command}");
    }
}

#[test]
fn declarations_and_braces_and_propagation_are_seen() {
    for command in [
        r#"export F="a b"; git add $F"#,
        r#"local F="a b"; git add ${F}"#,
        r#"F="a b"; G=$F; git add $G"#,
        r#"F=a; F+=" b"; git add $F"#,
        r#"F="a b"; ( cd x && git add $F )"#,
        "F=\"a b\"\nif true; then git add $F; fi",
        r#"F="a\ b"; git add -- x$F"#,
    ] {
        assert_eq!(findings(command).len(), 1, "{command}");
    }
}

#[test]
fn under_zsh_todays_commands_are_denied_naming_the_fixes() {
    for (command, var, _) in TODAY {
        let reason = judge_event(&event(command), &zsh_env).expect(command);
        for needle in [
            "array",
            "function",
            "inline",
            "# zsh-split-ok:",
            "/bin/zsh",
            var,
        ] {
            assert!(
                reason.contains(needle),
                "{command}: no {needle:?} in {reason}"
            );
        }
        let json: serde_json::Value = serde_json::from_str(&deny(&reason)).expect("json");
        assert_eq!(json["hookSpecificOutput"]["permissionDecision"], "deny");
    }
    let monitor =
        serde_json::json!({"tool_name": "Monitor", "tool_input": {"command": TODAY[0].0}});
    assert!(judge_event(&monitor, &zsh_env).is_some());
}

#[test]
fn under_zsh_the_passing_forms_and_other_tools_pass() {
    for command in PASSING {
        assert_eq!(judge_event(&event(command), &zsh_env), None, "{command}");
    }
    let write = serde_json::json!({"tool_name": "Write", "tool_input": {"command": TODAY[0].0}});
    assert_eq!(judge_event(&write, &zsh_env), None);
    assert_eq!(judge_event(&serde_json::json!({}), &zsh_env), None);
}

#[test]
fn under_bash_everything_passes() {
    for (command, _, _) in TODAY {
        assert_eq!(judge_event(&event(command), &bash_env), None, "{command}");
    }
    for command in PASSING {
        assert_eq!(judge_event(&event(command), &bash_env), None, "{command}");
    }
}

#[test]
fn the_escape_needs_a_reason() {
    let with = format!("{} # zsh-split-ok: one file named 'a b'", TODAY[0].0);
    assert_eq!(judge_event(&event(&with), &zsh_env), None);
    let bare = format!("{} # zsh-split-ok:", TODAY[0].0);
    assert!(judge_event(&event(&bare), &zsh_env).is_some());
    assert!(escaped("x #zsh-split-ok: why"));
    assert!(!escaped("x # zsh-split-ok:\nnext"));
}

#[test]
fn the_tool_shell_is_claude_code_shell_else_shell() {
    assert_eq!(tool_shell(&zsh_env), "/bin/zsh");
    assert_eq!(tool_shell(&bash_env), "/opt/homebrew/bin/bash");
    let empty = |k: &str| match k {
        "CLAUDE_CODE_SHELL" => Some(String::new()),
        "SHELL" => Some("/bin/zsh".to_owned()),
        _ => None,
    };
    assert_eq!(tool_shell(&empty), "/bin/zsh");
    assert!(is_zsh("/bin/zsh") && is_zsh("/opt/homebrew/bin/zsh-5.9"));
    assert!(!is_zsh("/opt/homebrew/bin/bash") && !is_zsh(""));
}

// ---- goh shell-check ----

fn good() -> Probed {
    Probed {
        bash_major: Some(5),
        bash_version: "5.3.0(1)-release".to_owned(),
        goh_dir: "/g".to_owned(),
        pythonpath: "/x:/g".to_owned(),
        agent_env_loaded: "1".to_owned(),
    }
}

fn run_check(vars: &[(&str, &str)], probed: Probed) -> Result<String, Vec<String>> {
    let map: BTreeMap<String, String> = vars
        .iter()
        .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
        .collect();
    let env = move |k: &str| map.get(k).cloned();
    let is_file = |p: &Path| p == Path::new("/h/.bashrc");
    let probe = move |_: &str, _: Option<&str>, _: &str| Ok(probed.clone());
    check(&Inputs {
        env: &env,
        is_file: &is_file,
        probe: &probe,
        ancestors: &super::observed::no_ancestors,
    })
}

const SET: [(&str, &str); 3] = [
    ("HOME", "/h"),
    ("CLAUDE_CODE_SHELL", "/opt/homebrew/bin/bash"),
    ("BASH_ENV", "/h/.bashrc"),
];

fn problems(result: Result<String, Vec<String>>) -> String {
    result.expect_err("refused").join("\n")
}

#[test]
fn shell_check_passes_bash5_with_the_house_env() {
    let line = run_check(&SET, good()).expect("passes");
    assert!(
        line.contains("bash 5.3") && line.contains("GOH_DIR=/g"),
        "{line}"
    );
}

#[test]
fn shell_check_refuses_an_unset_or_zsh_or_old_shell() {
    let unset = problems(run_check(&[SET[0], SET[2], ("SHELL", "/bin/zsh")], good()));
    assert!(
        unset.contains("CLAUDE_CODE_SHELL is not set") && unset.contains("/bin/zsh"),
        "{unset}"
    );
    let zsh = Probed {
        bash_major: None,
        ..good()
    };
    let not_bash = problems(run_check(&SET, zsh));
    assert!(not_bash.contains("is not bash"), "{not_bash}");
    let old = Probed {
        bash_major: Some(3),
        bash_version: "3.2.57".to_owned(),
        ..good()
    };
    assert!(problems(run_check(&SET, old)).contains("older than 5"));
}

#[test]
fn shell_check_refuses_a_missing_bash_env_or_an_env_without_goh_dir() {
    assert!(problems(run_check(&SET[..2], good())).contains("BASH_ENV is not set"));
    let elsewhere = [SET[0], SET[1], ("BASH_ENV", "/nowhere")];
    assert!(problems(run_check(&elsewhere, good())).contains("is not a file"));
    let no_goh = Probed {
        goh_dir: String::new(),
        ..good()
    };
    assert!(problems(run_check(&SET, no_goh)).contains("has no GOH_DIR"));
    let no_path = Probed {
        pythonpath: "/x".to_owned(),
        ..good()
    };
    assert!(problems(run_check(&SET, no_path)).contains("without GOH_DIR"));
    let unloaded = Probed {
        agent_env_loaded: String::new(),
        ..good()
    };
    assert!(problems(run_check(&SET, unloaded)).contains("AGENT_ENV_LOADED"));
}

#[test]
fn the_probe_output_parses_and_a_short_one_is_empty() {
    assert_eq!(
        parse_probe(b"5\x005.3\x00/g\x00/g\x001\x00"),
        Probed {
            bash_major: Some(5),
            bash_version: "5.3".to_owned(),
            goh_dir: "/g".to_owned(),
            pythonpath: "/g".to_owned(),
            agent_env_loaded: "1".to_owned(),
        }
    );
    assert_eq!(parse_probe(b""), Probed::default());
}

#[test]
fn the_real_probe_reads_bash_env_in_an_empty_environment() {
    let dir = tempfile::tempdir().expect("tmp");
    let rc = dir.path().join("rc");
    std::fs::write(&rc, "export GOH_DIR=/g PYTHONPATH=/g AGENT_ENV_LOADED=1\n").expect("rc");
    let home = dir.path().to_str().expect("utf8");
    let probed = real_probe("bash", rc.to_str(), home).expect("bash runs");
    assert!(probed.bash_major.is_some(), "{probed:?}");
    assert_eq!(
        (probed.goh_dir.as_str(), probed.agent_env_loaded.as_str()),
        ("/g", "1")
    );
    // No BASH_ENV: nothing inherited reaches the probe (the hook's own GOH_DIR is not read).
    let bare = real_probe("bash", None, home).expect("bash runs");
    assert_eq!(bare.goh_dir, "");
    assert!(real_probe("/no/such/shell", None, home).is_err());
}

#[test]
fn shell_check_names_a_shell_that_cannot_be_probed() {
    let map: BTreeMap<String, String> = SET
        .iter()
        .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
        .collect();
    let env = move |k: &str| map.get(k).cloned();
    let is_file = |p: &Path| p == Path::new("/h/.bashrc");
    let probe = |_: &str, _: Option<&str>, _: &str| Err("cannot run it".to_owned());
    let refused = problems(check(&Inputs {
        env: &env,
        is_file: &is_file,
        probe: &probe,
        ancestors: &super::observed::no_ancestors,
    }));
    assert!(
        refused
            .contains("CLAUDE_CODE_SHELL=/opt/homebrew/bin/bash cannot be probed (cannot run it)")
            && refused.contains("brew install bash"),
        "{refused}"
    );
    // Only that one problem: a shell that cannot run has nothing else to judge.
    assert_eq!(refused.lines().count(), 1, "{refused}");
}

#[test]
fn a_probe_that_outlives_its_ceiling_is_refused_naming_the_shell() {
    use std::os::unix::fs::PermissionsExt;
    let dir = tempfile::tempdir().expect("tmp");
    let slow = dir.path().join("slow-shell");
    std::fs::write(&slow, "#!/bin/sh\nexec sleep 30\n").expect("script");
    std::fs::set_permissions(&slow, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    let shell = slow.to_str().expect("utf8");
    let home = dir.path().to_str().expect("utf8");
    let started = std::time::Instant::now();
    let why = probe_within(shell, None, home, None, std::time::Duration::from_secs(1))
        .expect_err("hits the ceiling");
    assert_eq!(why, format!("{shell} -c did not finish in 1s"));
    assert!(
        started.elapsed().as_secs() < 20,
        "the run was cut, not awaited"
    );
}

// ---- goh zsh-split-guard's stdin ----

/// A reader that fails, as a closed or non-text stdin does.
struct Broken;

impl std::io::Read for Broken {
    fn read(&mut self, _: &mut [u8]) -> std::io::Result<usize> {
        Err(std::io::Error::other("stdin is gone"))
    }
}

#[test]
fn the_guard_prints_a_denial_only_for_a_readable_refused_event() {
    let refused = event(TODAY[0].0).to_string();
    let out = guard_output(refused.as_bytes(), &zsh_env).expect("denied");
    let json: serde_json::Value = serde_json::from_str(&out).expect("json");
    assert_eq!(json["hookSpecificOutput"]["permissionDecision"], "deny");
    // The same event under bash, an event that is no JSON, and a stdin that cannot be read: the
    // guard never breaks a command, so each prints nothing.
    assert_eq!(guard_output(refused.as_bytes(), &bash_env), None);
    assert_eq!(guard_output(&b"not json"[..], &zsh_env), None);
    assert_eq!(guard_output(&b""[..], &zsh_env), None);
    assert_eq!(guard_output(&[0xff_u8, 0xfe][..], &zsh_env), None);
    assert_eq!(guard_output(Broken, &zsh_env), None);
}

#[test]
fn the_probe_passes_the_user_only_when_there_is_one() {
    use std::os::unix::fs::PermissionsExt;
    let dir = tempfile::tempdir().expect("tmp");
    // A shell that reports the USER it was started with in the version field.
    let echo = dir.path().join("echo-user");
    std::fs::write(
        &echo,
        "#!/bin/sh\nu=none; [ -z \"$USER\" ] || u=$USER\nprintf '5\\0%s\\0\\0\\0\\0' \"$u\"\n",
    )
    .expect("script");
    std::fs::set_permissions(&echo, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    let shell = echo.to_str().expect("utf8");
    let home = dir.path().to_str().expect("utf8");
    let ceiling = std::time::Duration::from_secs(20);
    let named = probe_within(shell, None, home, Some("alice"), ceiling).expect("runs");
    assert_eq!(
        (named.bash_major, named.bash_version.as_str()),
        (Some(5), "alice")
    );
    let anonymous = probe_within(shell, None, home, None, ceiling).expect("runs");
    assert_eq!(
        anonymous.bash_version, "none",
        "nothing inherited reaches the probe"
    );
}
