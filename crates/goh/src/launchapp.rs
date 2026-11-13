//! `goh launch-app [--background] TARGET`: start an app with launchd's LOGIN environment, never
//! the caller's (GOH.CLEANOPEN).
//!
//! macOS `open` hands the app it launches its own environment, and the app outlives the caller
//! (PPID 1), so whatever the caller carried lives on in the app and in everything the app starts.
//! A ship's release started `ZoneWM` with `GOH_SHIP_ITEM` and a `GOH_DIR` naming a transient gate
//! export with no `bin/`; `Claude.app`, relaunched through `ZoneWM`, gave both to every agent of that
//! session (2026-11-13). The probe (a throwaway app writing its environ, a sentinel set in the
//! caller; `tests/test_launch_app.py` keeps it):
//!
//! | launch                                       | sentinel | caller's `GOH_DIR` | env vars |
//! |----------------------------------------------|----------|--------------------|----------|
//! | Finder `open` (the login reference)          | absent   | absent           | 16       |
//! | `open -a APP`, `open -n -a APP`              | present  | present          | 122      |
//! | `open --env SENTINEL= -a APP`                | present  | present          | 122      |
//! | `osascript 'tell application APP to launch'` | present  | present          | 122      |
//! | `launchctl asuser UID open -a APP`           | present  | present          | 123      |
//! | `env -i /usr/bin/open -a APP`                | absent   | absent           | 17       |
//!
//! `env -i` is the Finder's set plus `__CF_USER_TEXT_ENCODING`, which `open`'s own CoreFoundation
//! sets: launchd fills `HOME`, `PATH`, `TMPDIR`, `SSH_AUTH_SOCK` and every `launchctl setenv`. So
//! this clears the environment and runs `/usr/bin/open`: `--background` is `-g`, `--new` `-n`,
//! `--wait` `-W`, a bare NAME `-a NAME`, and what follows `--` the app's `--args`.
//! `GOH_LAUNCH_APP_OPEN` names another opener, for a test (read before the clear).

use goh_tui::ERR;
use std::ffi::OsString;
use std::path::Path;

const OPEN: &str = "/usr/bin/open";

/// How to launch: `open`'s `-g`, `-n`, `-W`, and the app's own arguments (`--args ...`).
#[derive(Debug, Default, Clone)]
pub struct Opts {
    /// `-g`: do not bring the app to the front.
    pub background: bool,
    /// `-n`: a new instance even if one runs.
    pub new: bool,
    /// `-W`: wait for the app to exit.
    pub wait: bool,
    /// Passed to the app after `--args`.
    pub args: Vec<OsString>,
}

/// The opener and its arguments. A TARGET with no `/` that is not a path is an app NAME
/// (`open -a Ghostty`); anything else is opened as a path (`open X.app`, a document).
#[must_use]
pub fn argv(opener: &str, target: &Path, o: &Opts) -> Vec<OsString> {
    let mut out = vec![OsString::from(opener)];
    for (on, flag) in [(o.background, "-g"), (o.new, "-n"), (o.wait, "-W")] {
        if on {
            out.push(flag.into());
        }
    }
    if is_name(target) {
        out.push("-a".into());
    }
    out.push(target.as_os_str().to_owned());
    if !o.args.is_empty() {
        out.push("--args".into());
        out.extend(o.args.iter().cloned());
    }
    out
}

fn is_name(target: &Path) -> bool {
    !target.as_os_str().to_string_lossy().contains('/') && !target.exists()
}

/// Run `argv` with an EMPTY environment; its exit code.
fn launch(argv: &[OsString]) -> i32 {
    std::process::Command::new(&argv[0])
        .args(&argv[1..])
        .env_clear()
        .stdin(std::process::Stdio::null())
        .status()
        .map_or_else(
            |e| {
                goh_tui::eoutln!(
                    "{ERR} goh launch-app: cannot run {}: {e}",
                    argv[0].display()
                );
                2
            },
            |s| s.code().unwrap_or(2),
        )
}

/// `goh launch-app [--background] [--new] [--wait] TARGET [-- ARGS...]`.
#[must_use]
pub fn run_command(target: &Path, o: &Opts) -> i32 {
    let opener = std::env::var("GOH_LAUNCH_APP_OPEN").unwrap_or_else(|_| OPEN.to_string());
    if opener == OPEN && !cfg!(target_os = "macos") {
        goh_tui::eoutln!("{ERR} goh launch-app: macOS only (LaunchServices' open)");
        return 2;
    }
    if !is_name(target) && !target.exists() {
        goh_tui::eoutln!("{ERR} goh launch-app: no such app: {}", target.display());
        return 2;
    }
    launch(&argv(&opener, target, o))
}

/// `run_command` over the parsed flags.
#[must_use]
pub fn run_args(a: crate::cli::LaunchAppArgs) -> i32 {
    let o = Opts {
        background: a.background,
        new: a.new,
        wait: a.wait,
        args: a.args,
    };
    run_command(&a.target, &o)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_flags_come_before_the_target_and_the_apps_args_after() {
        let o = Opts {
            background: true,
            ..Opts::default()
        };
        let a = argv(OPEN, Path::new("/Applications/X.app"), &o);
        assert_eq!(
            a,
            ["/usr/bin/open", "-g", "/Applications/X.app"].map(OsString::from)
        );
        let a = argv(OPEN, Path::new("/Applications/X.app"), &Opts::default());
        assert_eq!(
            a,
            ["/usr/bin/open", "/Applications/X.app"].map(OsString::from)
        );
        let o = Opts {
            new: true,
            wait: true,
            args: vec!["-e".into(), "x.sh".into()],
            ..Opts::default()
        };
        let a = argv(OPEN, Path::new("NoSuchAppName"), &o);
        let want = [
            "/usr/bin/open",
            "-n",
            "-W",
            "-a",
            "NoSuchAppName",
            "--args",
            "-e",
            "x.sh",
        ];
        assert_eq!(a, want.map(OsString::from));
    }
}
