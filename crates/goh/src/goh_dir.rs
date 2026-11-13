//! A `GOH_DIR` naming a REMOVED HEAD export is repaired before anything reads it (GOH.TRANSIENTDIR).
//!
//! The native third of one rule; `lib/goh_root.sh` (`goh_dir_repair`, also copied into every stock
//! hook) and `lib/goh_root.py` are the others, and `tests/test_dead_goh_dir.py` pins all three.

use std::path::PathBuf;

/// Where a `GOH_DIR` that is not a directory should point; `None` when it is fine.
///
/// Unset, or a directory, is fine. Every gate entry point exports `GOH_DIR` at a HEAD export under
/// `~/.cache/goh/head/`, the cache prunes old exports, and a long-lived process started under a
/// gate keeps the value: Routines.app's jobs and a Claude session's commits failed for hours once
/// theirs was gone. The export cannot be read, so the rule is `lib/goh_root.sh`'s: `GOH_LIVE_ROOT`,
/// then the default checkout, whichever holds `gates/`.
fn repaired(
    goh_dir: Option<&std::ffi::OsStr>,
    live: Option<&std::ffi::OsStr>,
    home: Option<&std::ffi::OsStr>,
    is_dir: impl Fn(&std::path::Path) -> bool,
) -> Option<PathBuf> {
    let dead = std::path::Path::new(goh_dir.filter(|d| !d.is_empty())?);
    if is_dir(dead) {
        return None;
    }
    let default = home.map(|h| PathBuf::from(h).join("Projects/gates_of_heck"));
    [live.filter(|l| !l.is_empty()).map(PathBuf::from), default]
        .into_iter()
        .flatten()
        .find(|c| is_dir(&c.join("gates")))
}

/// Re-runs this binary with `GOH_DIR` repaired when it names a removed export.
///
/// So every path it resolves AND every process it starts sees the live goh. Returns only when
/// nothing needed doing or the re-run could not start.
pub fn repair() {
    let goh_dir = std::env::var_os("GOH_DIR");
    let Some(fix) = repaired(
        goh_dir.as_deref(),
        std::env::var_os("GOH_LIVE_ROOT").as_deref(),
        std::env::var_os("HOME").as_deref(),
        std::path::Path::is_dir,
    ) else {
        return;
    };
    goh_tui::eoutln!(
        "{} gates_of_heck: GOH_DIR={} no longer exists (a pruned HEAD export this process \
         inherited; restart it) -- using {}",
        goh_tui::WARN,
        goh_dir.unwrap_or_default().to_string_lossy(),
        fix.display()
    );
    let Ok(me) = std::env::current_exe() else {
        return;
    };
    let err = std::os::unix::process::CommandExt::exec(
        std::process::Command::new(me)
            .args(std::env::args_os().skip(1))
            .env("GOH_DIR", &fix),
    );
    goh_tui::eoutln!(
        "{} goh: could not re-run with GOH_DIR={}: {err}",
        goh_tui::ERR,
        fix.display()
    );
}

#[cfg(test)]
mod goh_dir_tests {
    use super::repaired;
    use std::ffi::OsStr;
    use std::path::{Path, PathBuf};

    fn dirs(have: &'static [&'static str]) -> impl Fn(&Path) -> bool {
        move |p| have.iter().any(|h| Path::new(h) == p)
    }

    #[test]
    fn a_dead_export_falls_back_to_the_live_root_then_the_default() {
        let dead = Some(OsStr::new("/c/goh/head/c06bc794"));
        let (live, home) = (Some(OsStr::new("/live")), Some(OsStr::new("/h")));
        let both = dirs(&["/live/gates", "/h/Projects/gates_of_heck/gates"]);
        assert_eq!(
            repaired(dead, live, home, &both),
            Some(PathBuf::from("/live"))
        );
        let only_default = dirs(&["/h/Projects/gates_of_heck/gates"]);
        let want = Some(PathBuf::from("/h/Projects/gates_of_heck"));
        assert_eq!(repaired(dead, live, home, &only_default), want);
        assert_eq!(repaired(dead, None, home, &only_default), want);
        assert_eq!(repaired(dead, live, home, dirs(&[])), None);
    }

    #[test]
    fn a_live_or_unset_goh_dir_is_left_alone() {
        let home = Some(OsStr::new("/h"));
        let all = dirs(&["/x", "/h/Projects/gates_of_heck/gates"]);
        assert_eq!(repaired(Some(OsStr::new("/x")), None, home, &all), None);
        assert_eq!(repaired(None, None, home, &all), None);
        assert_eq!(repaired(Some(OsStr::new("")), None, home, &all), None);
    }
}
