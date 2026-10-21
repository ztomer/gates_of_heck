//! `goh js-syntax` -- every JavaScript source this repository tracks PARSES.
//!
//! THE SUBJECT. The JavaScript files `git ls-files` lists: `.js`, `.mjs` and
//! `.cjs`. Not the working tree (an untracked file is not the repository yet),
//! and not vendored or built output (which the scope names, so the exclusion
//! cannot hide the subject -- see `Scope::shape`).
//!
//! THE SCOPE IS STATED, NOT IMPLIED. A reader is told how many files were
//! measured and what shape they had, because a gate that quietly measures
//! nothing and a gate that measured everything and found nothing wrong print
//! the same word. See `report` for what every run prints.
//!
//! TWO ENGINES, AND WHY. `node --check` parses without running, and knows a
//! module from a script by itself. `JavaScriptCore`, through the `jsc` helper
//! macOS ships, is the fallback for a machine with no node at all: it parses
//! through `new Function`, which builds a function from the source WITHOUT
//! RUNNING IT. See `engines` for what each can and cannot measure.
//!
//! THE TEMPLATE RULE, in one line: a template is judged by its SUBSTITUTED
//! form, and its placeholders are the ones the tree's own convention defines.
//! See `template` for why a template is not excluded (that hides the subject)
//! and why the substitution is narrow (a wide one would hide a defect).
//!
//! WHAT A CLEAN RUN SAYS. The scope, the engine, the count, and the templates
//! that were judged by their substituted form. What a red run says: every
//! violation as `file:line: why`, and every source the engine could not
//! measure, with the reason it could not.

pub mod command;
pub mod engines;
pub mod report;
pub mod scan;
pub mod template;

use std::path::{Path, PathBuf};

use engines::{Check, Verdict};

/// The tag every line this check prints carries.
const TAG: &str = "[js_syntax]";

/// The extensions in scope.
pub const EXTENSIONS: [&str; 3] = ["js", "mjs", "cjs"];

/// Path segments that mean "not our source": vendored code, or built output.
///
/// A SEGMENT, never a substring: `src/build_tools.js` is source, and
/// `build/tools.js` is not. Every path this list takes out of the scope is
/// named in the report, so an exclusion that hid a real file is visible rather
/// than silent.
pub const EXCLUDED_SEGMENTS: [&str; 10] = [
    "node_modules",
    "vendor",
    "vendored",
    "dist",
    "build",
    "out",
    "target",
    "coverage",
    ".next",
    ".cache",
];

/// One file in scope.
#[derive(Debug, Clone)]
pub struct Subject {
    /// The repo-relative path.
    path: String,
    /// Is this a template, by the tree's own naming convention?
    template: bool,
}

/// What the check measured.
#[derive(Debug, Default, Clone)]
pub struct Scope {
    /// The tracked JavaScript files.
    pub tracked: usize,
    /// The paths the vendor/build list took out of the scope.
    pub excluded: Vec<String>,
    /// The files in scope.
    pub files: Vec<Subject>,
}

impl Scope {
    /// The scope's shape: the top-level tree each file sits in, with its count.
    #[must_use]
    pub fn shape(&self) -> Vec<(String, usize)> {
        let mut counts: Vec<(String, usize)> = Vec::new();
        for file in &self.files {
            let top = file
                .path
                .split_once('/')
                .map_or_else(|| ".".to_owned(), |(head, _)| head.to_owned());
            match counts.iter_mut().find(|(name, _)| *name == top) {
                Some((_, count)) => *count += 1,
                None => counts.push((top, 1)),
            }
        }
        counts.sort();
        counts
    }
}

/// One thing the check found.
#[derive(Debug, Clone)]
pub struct Finding {
    /// The repo-relative path.
    pub path: String,
    /// The line the defect is on, when the engine could say.
    pub line: Option<usize>,
    /// Why it is a finding.
    pub reason: String,
}

/// What one run found.
#[derive(Debug, Default)]
pub struct Outcome {
    /// What was measured.
    pub scope: Scope,
    /// The engine that measured it.
    pub engine: String,
    /// How many sources were handed to the engine.
    pub judged: usize,
    /// How many of them were templates, judged by their substituted form.
    pub templates: usize,
    /// The violations.
    pub findings: Vec<Finding>,
    /// The sources the engine could not measure, and why.
    pub unmeasured: Vec<(String, String)>,
}

/// The temporary files a run made, and their removal.
///
/// A template is judged by its SUBSTITUTED form, so the engine reads a file
/// that is not the repository's file. It is written BESIDE the original -- the
/// same directory, so the nearest `package.json` is the one the original would
/// have been judged against -- under a name no tool collects, and it is removed
/// when the run ends, however the run ends.
#[derive(Debug, Default)]
struct Temps(Vec<PathBuf>);

impl Drop for Temps {
    fn drop(&mut self) {
        for path in &self.0 {
            let _ = std::fs::remove_file(path);
        }
    }
}

/// The scope: the tracked JavaScript files, minus the vendored and built ones.
#[must_use]
pub fn scope(tracked: &[String]) -> Scope {
    let mut out = Scope::default();
    for path in tracked {
        if !is_javascript(path) {
            continue;
        }
        out.tracked += 1;
        if excluded(path) {
            out.excluded.push(path.clone());
            continue;
        }
        out.files.push(Subject {
            path: path.clone(),
            template: template::is_template(path),
        });
    }
    out
}

/// Is this path a JavaScript source?
fn is_javascript(path: &str) -> bool {
    let name = path.rsplit('/').next().unwrap_or(path);
    let extension = name.rsplit_once('.').map_or("", |(_, ext)| ext);
    EXTENSIONS
        .iter()
        .any(|known| extension.eq_ignore_ascii_case(known))
}

/// Does this path sit under a vendored or built segment?
fn excluded(path: &str) -> bool {
    path.split('/')
        .any(|segment| EXCLUDED_SEGMENTS.contains(&segment))
}

/// Read every source in scope and hand it to the engine.
///
/// # Errors
///
/// Returns a message when the repository cannot be read, or when the engine
/// cannot be run: a gate that cannot parse is a gate that cannot judge.
pub fn run(root: &Path) -> Result<Outcome, String> {
    let Some(engine) = engines::discover() else {
        return Err(
            "no JavaScript engine on this machine: node is not installed and macOS's \
             JavaScriptCore helper was not found"
                .to_owned(),
        );
    };
    let tracked = crate::gitutil::index_files(root)?;
    let scope = scope(&tracked);
    let mut outcome = Outcome {
        engine: engine.label(),
        scope: scope.clone(),
        ..Outcome::default()
    };
    if scope.files.is_empty() {
        return Ok(outcome);
    }

    let mut temps = Temps::default();
    let mut checks = Vec::with_capacity(scope.files.len());
    for (index, file) in scope.files.iter().enumerate() {
        let Some(bytes) = crate::gitutil::content_bytes(root, &file.path, false) else {
            outcome
                .unmeasured
                .push((file.path.clone(), "the file could not be read".to_owned()));
            continue;
        };
        if bytes[..bytes.len().min(crate::gitutil::BINARY_SCAN_WINDOW)].contains(&0) {
            // A binary named `.js` is not source.
            continue;
        }
        let text = String::from_utf8_lossy(&bytes).into_owned();
        if !engine.measures_modules() && scan::is_module(&text) {
            // The engine cannot read this goal without running the module, and
            // a gate does not run the repository's code. Named, never passed.
            outcome.unmeasured.push((
                file.path.clone(),
                "this engine parses a classic script only: a module source is read by \
                 `node --check`, which parses it without running it"
                    .to_owned(),
            ));
            continue;
        }
        let judged = if file.template {
            outcome.templates += 1;
            template::substitute(&text).text
        } else {
            text.clone()
        };
        let check_path = if judged == text {
            root.join(&file.path)
        } else {
            match write_temp(root, &file.path, index, &judged) {
                Ok(path) => {
                    temps.0.push(path.clone());
                    path
                }
                Err(message) => {
                    outcome.unmeasured.push((file.path.clone(), message));
                    continue;
                }
            }
        };
        checks.push(Check {
            path: file.path.clone(),
            check_path,
        });
    }

    outcome.judged = checks.len();
    let verdicts = engine.check_all(&checks)?;
    for (check, verdict) in checks.iter().zip(verdicts) {
        match verdict {
            Verdict::Clean => {}
            Verdict::Syntax { line, message } => {
                let reason = if template::is_template(&check.path) {
                    format!("the substituted form does not parse: {message}")
                } else {
                    format!("does not parse: {message}")
                };
                outcome.findings.push(Finding {
                    path: check.path.clone(),
                    line,
                    reason,
                });
            }
            Verdict::Unmeasured(why) => {
                outcome.unmeasured.push((check.path.clone(), why));
            }
        }
    }
    Ok(outcome)
}

/// Write a template's substituted form beside the original, and say where.
///
/// # Errors
///
/// Returns a message when the file could not be written: a template whose
/// substituted form could not be handed to the engine is a source that was not
/// measured, and it says so rather than passing.
fn write_temp(root: &Path, rel: &str, index: usize, text: &str) -> Result<PathBuf, String> {
    let original = root.join(rel);
    let extension = original
        .extension()
        .map_or_else(String::new, |e| format!(".{}", e.to_string_lossy()));
    let name = format!(".goh-js-syntax.{}.{index}{extension}", std::process::id());
    let path = original.with_file_name(name);
    std::fs::write(&path, text)
        .map_err(|e| format!("could not write its substituted form: {e}"))?;
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::{excluded, is_javascript, scope, Scope};

    #[test]
    fn the_extensions_are_the_javascript_ones() {
        assert!(is_javascript("a.js"));
        assert!(is_javascript("a.mjs"));
        assert!(is_javascript("a.cjs"));
        assert!(is_javascript("dir/a.JS"));
        assert!(!is_javascript("a.json"));
        assert!(!is_javascript("a.jsx"));
        assert!(!is_javascript("a.ts"));
        assert!(!is_javascript("js"));
    }

    #[test]
    fn a_vendored_or_built_path_is_out() {
        assert!(excluded("node_modules/x/a.js"));
        assert!(excluded("web/dist/a.js"));
        assert!(excluded("crates/target/debug/a.js"));
        assert!(!excluded("src/build_tools.js"));
        assert!(!excluded("src/a.js"));
    }

    #[test]
    fn the_scope_counts_and_names_what_it_excluded() {
        let tracked = vec![
            "web/a.js".to_owned(),
            "web/templates_x.js".to_owned(),
            "node_modules/p/b.js".to_owned(),
            "README.md".to_owned(),
        ];
        let scope = scope(&tracked);
        assert_eq!(scope.tracked, 3);
        assert_eq!(scope.excluded, vec!["node_modules/p/b.js".to_owned()]);
        assert_eq!(scope.files.len(), 2);
        assert_eq!(scope.shape(), vec![("web".to_owned(), 2)]);
    }

    #[test]
    fn an_empty_scope_has_no_shape() {
        assert_eq!(Scope::default().shape().len(), 0);
    }
}
