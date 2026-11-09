//! `mktemp -t NAME` with no X in NAME is BSD-only (`goh mktemp-portable`, GOH.MKTEMPPORT).
//!
//! BSD (macOS) `mktemp -t prefix` appends its own random suffix; GNU `mktemp -t TEMPLATE` reads
//! TEMPLATE as a template and refuses one with fewer than three trailing X ("too few X's in
//! template"). So a script written and tested on the Mac fails on Linux CI before the command it
//! guards runs: `monitor`'s release.sh did (`log="$(mktemp -t "release-step")"`, M.RELFAIL,
//! 2026-11-08). The portable form names its directory and its X's:
//! `mktemp "${TMPDIR:-/tmp}/NAME.XXXXXX"` (and `mktemp -d ...` for a directory).
//!
//! WHAT IS JUDGED: every `mktemp` in a shell source (`crate::shellsrc`) whose options carry `t`
//! (`-t`, `-d -t`, `-dt`, `-tq`) and whose template, the first operand, holds no `XXX`. A template
//! that is a variable alone (`mktemp -t "$name"`) is judged as written: nothing in the line shows
//! its X's. Comment lines are not read.
//!
//! WIRING: a structural step in EVERY repo. The estate's sites on the day it landed are seeded in
//! `mktemp_port_seed.json` (`crate::estate_seed`): seeded files warn, anything else is red.

use crate::estate_seed::{self, Kind, Report, Sites};
use std::path::Path;

const TAG: &str = "[mktemp_portable]";
/// The step's label, the same line as in `structural_steps.txt`.
pub const LABEL: &str = "no BSD-only mktemp -t";

/// The seed of the estate's sites on the day the check landed.
pub const KIND: Kind = Kind {
    file: "mktemp_port_seed.json",
    unit: "BSD-only mktemp",
    name: "mktemp seed",
};

const FIX: &str = "    BSD mktemp -t NAME appends its own random suffix; GNU mktemp reads NAME as a template and\n    \
     refuses one with no X (\"too few X's in template\"), so a Linux CI run fails before the command\n    \
     runs (monitor's release.sh, M.RELFAIL). Name the directory and the X's, the form both accept:\n      \
     mktemp \"${TMPDIR:-/tmp}/NAME.XXXXXX\"      (a file)\n      \
     mktemp -d \"${TMPDIR:-/tmp}/NAME.XXXXXX\"   (a directory)\n";

/// Each X-less `mktemp -t` in one line: the template as written (empty when it has none).
#[must_use]
pub fn scan_line(line: &str) -> Vec<String> {
    let t = line.trim_start();
    if t.starts_with('#') {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut from = 0;
    while let Some(k) = line[from..].find("mktemp") {
        let at = from + k;
        from = at + "mktemp".len();
        let before = line[..at].chars().next_back().unwrap_or(' ');
        let after = line[from..].chars().next().unwrap_or(' ');
        if !(before.is_whitespace() || "($\"'`;|&".contains(before)) || !after.is_whitespace() {
            continue;
        }
        let rest = &line[from..];
        let end = rest
            .find([')', ';', '|', '&', '`', '#'])
            .unwrap_or(rest.len());
        if let Some(tpl) = xless_t(&rest[..end]) {
            out.push(tpl);
        }
    }
    out
}

/// The template of a `-t` call with no `XXX`, from the words after `mktemp`; `None` when it is
/// not a `-t` call, or its template carries X's.
fn xless_t(args: &str) -> Option<String> {
    let words: Vec<&str> = args
        .split_whitespace()
        .map(|w| w.trim_matches(|c| c == '"' || c == '\''))
        .collect();
    let mut has_t = false;
    let mut template: Option<&str> = None;
    let mut i = 0;
    while i < words.len() {
        let w = words[i];
        if w == "--" {
            template = template.or_else(|| words.get(i + 1).copied());
            break;
        }
        if let Some(flags) = w
            .strip_prefix('-')
            .filter(|f| !f.starts_with('-') && !f.is_empty())
        {
            if flags.contains('t') {
                has_t = true;
            }
            if flags.ends_with('p') {
                i += 1; // `-p DIR` takes the next word
            }
        } else if !w.starts_with("--") && template.is_none() {
            template = Some(w);
        }
        i += 1;
    }
    if !has_t {
        return None;
    }
    let tpl = template.unwrap_or("");
    (!tpl.contains("XXX")).then(|| tpl.to_string())
}

/// The sites in the shell sources among `files`.
fn sites(root: &Path, files: &[String], exclude: &str, staged: bool) -> Result<Sites, String> {
    let exclude = crate::steps::compile_exclude(exclude)?;
    let sources = crate::shellsrc::sources(root, files, exclude.as_ref(), staged);
    crate::subject::record(&crate::subject::MKTEMP_PORTABLE, sources.len());
    let mut out = Sites::new();
    for (rel, text) in &sources {
        let mut found = Vec::new();
        for (n, line) in text.lines().enumerate() {
            for tpl in scan_line(line) {
                let what = if tpl.is_empty() {
                    "no template".to_string()
                } else {
                    format!("template `{tpl}` has no X")
                };
                found.push(format!(
                    "    {rel}:{}: {}  -- mktemp -t, {what}: GNU refuses it",
                    n + 1,
                    estate_seed::clip(line)
                ));
            }
        }
        if !found.is_empty() {
            out.insert(rel.clone(), found);
        }
    }
    Ok(out)
}

/// The verdict over `files`: `(exit code, stdout, stderr)`.
fn judge(root: &Path, files: &[String], exclude: &str, staged: bool) -> (i32, String, String) {
    use goh_tui::ERR;
    let seed = match estate_seed::load(root, KIND) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let found = match sites(root, files, exclude, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let report = Report {
        tag: TAG,
        item: "GOH.MKTEMPPORT",
        what: (
            "BSD-only mktemp -t call with no X in the template",
            "BSD-only mktemp -t calls with no X in the template",
        ),
        fix: FIX,
        ok: "every mktemp -t names its X's".to_string(),
    };
    estate_seed::verdict(root, staged, KIND, &seed, &found, &report)
}

/// `goh mktemp-portable [--staged] [--exclude RE]`.
#[must_use]
pub fn run_command(staged: bool, exclude: &str) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        goh_tui::eoutln!("{} {TAG} not a git repository", goh_tui::ERR);
        return 2;
    };
    match crate::gitutil::listed_files(&root, staged) {
        Ok(files) => estate_seed::print(judge(&root, &files, exclude, staged)),
        Err(m) => {
            goh_tui::eoutln!("{} {TAG} {m}", goh_tui::ERR);
            2
        }
    }
}

/// The structural step, in every repo.
#[must_use]
pub fn step(
    repo: &Path,
    files: &[String],
    cfg: &crate::gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    estate_seed::step(
        LABEL,
        "crates/goh/src/mktempport.rs",
        judge(repo, files, &cfg.exclude, staged),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_bsd_only_forms_are_found() {
        assert_eq!(
            scan_line(r#"  local log; log="$(mktemp -t "release-step")""#),
            vec!["release-step"]
        );
        assert_eq!(scan_line("d=$(mktemp -d -t goh-sweep)"), vec!["goh-sweep"]);
        assert_eq!(scan_line("d=$(mktemp -dt goh)"), vec!["goh"]);
        assert_eq!(
            scan_line(r#"f=$(mktemp -t "$name") || exit"#),
            vec!["$name"]
        );
        assert_eq!(scan_line("f=`mktemp -t x`; g=`mktemp -t y`").len(), 2);
    }

    #[test]
    fn the_portable_and_unrelated_forms_pass() {
        for ok in [
            r#"log="$(mktemp "${TMPDIR:-/tmp}/release-step.XXXXXX")""#,
            r#"d="$(mktemp -d "${TMPDIR:-/tmp}/sweep.XXXXXX")""#,
            "f=$(mktemp -t release.XXXXXX)",
            "f=$(mktemp)",
            "d=$(mktemp -d)",
            "# log=$(mktemp -t release-step)",
            "echo mktemps -t x",
            "f=$(mktemp -p \"$dir\" x.XXXXXX)",
        ] {
            assert!(scan_line(ok).is_empty(), "{ok}");
        }
    }
}
