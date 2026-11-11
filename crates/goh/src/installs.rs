//! `GOH_INSTALLS` names installable artifacts in a form the installed-version reader reads
//! (`goh installs`, LOOP.INSTALLED2).
//!
//! A repo declares what it installs on this Mac in its `.gatesrc`: space-separated artifacts, each
//! an app bundle at `/Applications/...X.app` or `~/Applications/...X.app`, or a CLI by its bare
//! name (`GOH_INSTALLS='/Applications/Widget.app widget'`). The reader (`.loop/installs.py`) resolves an
//! app against /Applications or `$HOME` and a CLI against the house bin dirs, so any other spelling
//! -- a relative bundle, `/usr/local/bin/x`, a `..` segment, an empty value -- is a declaration that
//! verifies nothing and still reads like one. A repo with no key is not judged. A structural step
//! at both scopes (the key is one line of one file; there is nothing to scope).

use goh_tui::{ERR, OK};
use std::collections::BTreeSet;
use std::fmt::Write as _;

const TAG: &str = "[installs]";
const KEY: &str = "GOH_INSTALLS";

/// Why `word` is not an artifact the reader reads, or `None` when it is.
fn fault(word: &str) -> Option<&'static str> {
    if std::path::Path::new(word)
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("app"))
    {
        let rest = word
            .strip_prefix("/Applications/")
            .or_else(|| word.strip_prefix("~/Applications/"));
        return match rest {
            None => Some("an app is read only at /Applications/... or ~/Applications/..."),
            Some(r) if r.split('/').any(|s| s.is_empty() || s == "." || s == "..") => {
                Some("an app path has an empty, `.` or `..` segment")
            }
            Some(_) => None,
        };
    }
    let mut chars = word.chars();
    let lead = chars.next().is_some_and(|c| c.is_ascii_alphabetic());
    if lead && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-')) {
        None
    } else {
        Some("a CLI is named bare (`widget`), as the bin dirs list it")
    }
}

/// `(code, report)` for a `GOH_INSTALLS` value; `None` is no key.
fn judge(value: Option<&str>) -> (i32, String) {
    let Some(value) = value else {
        return (0, format!("{OK} {TAG} OK — no {KEY} declared\n"));
    };
    let words: Vec<&str> = value.split_whitespace().collect();
    if words.is_empty() {
        return (
            1,
            format!("{ERR} {TAG} {KEY} declares nothing: name each artifact, or delete the key\n"),
        );
    }
    let mut seen = BTreeSet::new();
    let mut bad = String::new();
    for w in &words {
        if !seen.insert(*w) {
            let _ = writeln!(bad, "    {w}: declared twice");
        } else if let Some(why) = fault(w) {
            let _ = writeln!(bad, "    {w}: {why}");
        }
    }
    if bad.is_empty() {
        let n = goh_tui::counted(words.len(), "readable artifact");
        return (0, format!("{OK} {TAG} OK — {KEY} names {n}\n"));
    }
    (
        1,
        format!("{ERR} {TAG} {KEY} has entries the installed-version reader cannot read:\n{bad}"),
    )
}

fn value(cfg: &crate::gatesrc::Gatesrc) -> Option<&str> {
    cfg.installs.as_deref()
}

/// `goh installs`.
#[must_use]
pub fn run_command() -> i32 {
    let repo = crate::gatesrc::config_root();
    let cfg = match crate::gatesrc::load(&repo) {
        Ok(cfg) => cfg,
        Err(m) => {
            goh_tui::eoutln!("{ERR} {TAG} {m}");
            return 2;
        }
    };
    let (code, report) = judge(value(&cfg));
    if code == 0 {
        goh_tui::out(&report);
    } else {
        goh_tui::eout(&report);
    }
    code
}

/// The structural step: silent with no key.
#[must_use]
pub fn step(cfg: &crate::gatesrc::Gatesrc) -> Option<i32> {
    value(cfg)?;
    let label = "GOH_INSTALLS names readable artifacts";
    let start = crate::step_report::begin(label);
    match judge(value(cfg)) {
        (0, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, report) => {
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/installs.rs"),
                &report,
                start,
            );
            Some(code)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{fault, judge};

    #[test]
    fn the_reader_forms_pass_and_others_do_not() {
        for ok in [
            "/Applications/Z.app",
            "/Applications/A/A.app",
            "~/Applications/T.app",
            "widget",
            "app_updates",
        ] {
            assert_eq!(fault(ok), None, "{ok}");
        }
        for bad in [
            "Z.app",
            "/opt/Z.app",
            "/Applications/../Z.app",
            "bin/widget",
            "-z",
            "",
        ] {
            assert!(fault(bad).is_some(), "{bad}");
        }
        assert_eq!(judge(Some("  ")).0, 1);
        assert_eq!(judge(Some("z z")).0, 1);
        assert_eq!(judge(None).0, 0);
    }
}
