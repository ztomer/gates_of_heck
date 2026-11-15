//! `goh launch-app [--background] TARGET | -b ID`: start an app with launchd's LOGIN environment, never
//! the caller's (GOH.CLEANOPEN).
//!
//! macOS `open` hands the app it launches its own environment, and the app outlives the caller
//! (PPID 1), so whatever the caller carried lives on in the app and in everything the app starts.
//! A ship's release started a private Swift app with `GOH_SHIP_ITEM` and a `GOH_DIR` naming a
//! transient gate export with no `bin/`; `Claude.app`, relaunched through a private Swift app, gave
//! both to every agent of that session (2026-11-13). The probe (a throwaway app writing its
//! environ, a sentinel set in the caller; `tests/test_launch_app.py` keeps it):
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
//! `--wait` `-W`, a bare NAME `-a NAME`, `--bundle-id ID` `-b ID` (GOH.LAUNCHAPPID: an id read
//! as a NAME ran `open -a com.example.App`, which fails), and what follows `--` the app's `--args`.
//! Python callers launch through `tui/launch.py`, which spells this command once.
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

/// What to launch: a TARGET (path or app NAME), or an app by its bundle identifier.
#[derive(Debug, Clone, Copy)]
pub enum Target<'a> {
    /// A `.app`, a document, or an app NAME.
    Path(&'a Path),
    /// `open -b ID`.
    BundleId(&'a str),
}

/// The opener and its arguments. A TARGET with no `/` that is not a path is an app NAME
/// (`open -a Ghostty`); anything else is opened as a path (`open X.app`, a document); a bundle id
/// is `open -b ID`.
#[must_use]
pub fn argv(opener: &str, target: Target<'_>, o: &Opts) -> Vec<OsString> {
    let mut out = vec![OsString::from(opener)];
    for (on, flag) in [(o.background, "-g"), (o.new, "-n"), (o.wait, "-W")] {
        if on {
            out.push(flag.into());
        }
    }
    match target {
        Target::BundleId(id) => {
            out.push("-b".into());
            out.push(id.into());
        }
        Target::Path(p) => {
            if is_name(p) {
                out.push("-a".into());
            }
            out.push(p.as_os_str().to_owned());
        }
    }
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

/// `goh launch-app [--background] [--new] [--wait] (TARGET | -b ID) [-- ARGS...]`.
#[must_use]
pub fn run_command(target: Target<'_>, o: &Opts) -> i32 {
    let opener = std::env::var("GOH_LAUNCH_APP_OPEN").unwrap_or_else(|_| OPEN.to_string());
    if opener == OPEN && !cfg!(target_os = "macos") {
        goh_tui::eoutln!("{ERR} goh launch-app: macOS only (LaunchServices' open)");
        return 2;
    }
    if let Target::Path(p) = target {
        if !is_name(p) && !p.exists() {
            goh_tui::eoutln!("{ERR} goh launch-app: no such app: {}", p.display());
            return 2;
        }
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
    match (&a.bundle_id, &a.target) {
        (Some(id), _) => run_command(Target::BundleId(id), &o),
        (None, Some(p)) => run_command(Target::Path(p), &o),
        (None, None) => {
            goh_tui::eoutln!("{ERR} goh launch-app: a TARGET or -b ID is required");
            2
        }
    }
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
        let a = argv(OPEN, Target::Path(Path::new("/Applications/X.app")), &o);
        assert_eq!(
            a,
            ["/usr/bin/open", "-g", "/Applications/X.app"].map(OsString::from)
        );
        let a = argv(
            OPEN,
            Target::Path(Path::new("/Applications/X.app")),
            &Opts::default(),
        );
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
        let a = argv(OPEN, Target::Path(Path::new("NoSuchAppName")), &o);
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

    #[test]
    fn a_bundle_id_is_open_b_never_a_name() {
        let o = Opts {
            background: true,
            ..Opts::default()
        };
        let a = argv(OPEN, Target::BundleId("com.google.GeminiMacOS"), &o);
        assert_eq!(
            a,
            ["/usr/bin/open", "-g", "-b", "com.google.GeminiMacOS"].map(OsString::from)
        );
    }
}
