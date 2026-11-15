//! A `GOH_DIR` naming a REMOVED HEAD export is repaired before anything reads it (GOH.TRANSIENTDIR),
//! and `GOH_LIVE_ROOT` is honoured only for an export (GOH.LIVEROOTLEAK).
//!
//! The native third of one rule; `lib/goh_root.sh` (`goh_is_export`, `goh_live_root_for`,
//! `goh_dir_repair`, the story) and `lib/goh_root.py` are the others, and
//! `tests/test_live_root_rule.py` runs one table through all three.

use std::path::{Path, PathBuf};

/// Where `gates/_from_head.sh` builds every HEAD export (`lib/goh_root.sh` `goh_head_cache`).
fn head_cache(env: &dyn Fn(&str) -> Option<std::ffi::OsString>) -> PathBuf {
    let set = |k: &str| env(k).filter(|v| !v.is_empty());
    if let Some(cache) = set("GOH_HEAD_CACHE") {
        return PathBuf::from(cache);
    }
    set("XDG_CACHE_HOME")
        .map_or_else(
            || PathBuf::from(env("HOME").unwrap_or_default()).join(".cache"),
            PathBuf::from,
        )
        .join("goh/head")
}

/// `dir` is a HEAD export (it holds `.goh-head`), or was one and has been pruned (it sat in the head
/// cache). The one definition every `GOH_LIVE_ROOT` reader asks (`lib/goh_root.sh` `goh_is_export`).
fn is_export_in(dir: &Path, cache: &Path, exists: impl Fn(&Path) -> bool) -> bool {
    dir.join(".goh-head").is_file() || (!exists(dir) && dir.parent() == Some(cache))
}

/// `GOH_LIVE_ROOT` when `dir` is an export, else `None`: the checkout an export was cut from, never
/// a value an ancestor's export leaked into a plain checkout's process (the v0.119.0 ship).
#[must_use]
pub fn live_root_for(dir: &Path) -> Option<PathBuf> {
    let env = |k: &str| std::env::var_os(k);
    let live = env("GOH_LIVE_ROOT").filter(|l| !l.is_empty())?;
    is_export_in(dir, &head_cache(&env), |p| p.symlink_metadata().is_ok()).then(|| live.into())
}

/// Where a `GOH_DIR` that is not a directory should point; `None` when it is fine.
///
/// Unset, or a directory, is fine. Every gate entry point exports `GOH_DIR` at a HEAD export under
/// `~/.cache/goh/head/`, the cache prunes old exports, and a long-lived process started under a
/// gate keeps the value: Routines.app's jobs and a Claude session's commits failed for hours once
/// theirs was gone. The export cannot be read, so the rule is `lib/goh_root.sh`'s: its live root
/// (`live`, already filtered by [`live_root_for`]), then the default checkout, whichever holds
/// `gates/`.
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
    let live = goh_dir
        .as_deref()
        .filter(|d| !d.is_empty())
        .and_then(|d| live_root_for(Path::new(d)));
    let Some(fix) = repaired(
        goh_dir.as_deref(),
        live.as_deref().map(Path::as_os_str),
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
    use super::{head_cache, is_export_in, repaired};
    use std::ffi::{OsStr, OsString};
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

    #[test]
    fn only_an_export_is_one() {
        let cache = Path::new("/c/goh/head");
        let gone = |_: &Path| false;
        assert!(is_export_in(Path::new("/c/goh/head/c06bc794"), cache, gone));
        assert!(!is_export_in(Path::new("/w/removed-lane"), cache, gone));
        assert!(!is_export_in(
            Path::new("/c/goh/head/c06bc794"),
            cache,
            |_| true
        ));
        assert!(!is_export_in(Path::new("/c/goh/head/a/b"), cache, gone));
    }

    #[test]
    fn the_head_cache_is_from_heads() {
        let env = |pairs: &'static [(&'static str, &'static str)]| {
            move |k: &str| {
                pairs
                    .iter()
                    .find(|(n, _)| *n == k)
                    .map(|(_, v)| OsString::from(v))
            }
        };
        let at = |e: &dyn Fn(&str) -> Option<OsString>| head_cache(e);
        assert_eq!(
            at(&env(&[("GOH_HEAD_CACHE", "/x"), ("HOME", "/h")])),
            PathBuf::from("/x")
        );
        assert_eq!(
            at(&env(&[("XDG_CACHE_HOME", "/xdg"), ("HOME", "/h")])),
            PathBuf::from("/xdg/goh/head")
        );
        assert_eq!(
            at(&env(&[("HOME", "/h")])),
            PathBuf::from("/h/.cache/goh/head")
        );
    }
}
