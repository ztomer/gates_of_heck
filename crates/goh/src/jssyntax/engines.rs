//! The two engines that parse JavaScript, and how they were chosen.
//!
//! THE GOAL COMES FIRST, and it is the BROWSER'S. A parser is asked "is this source valid?"
//! only once it has been told what KIND of source it is, and the goals disagree: a top-level
//! `return` is a syntax error in a browser's classic script and an ordinary statement in
//! `CommonJS`, and a top-level `await` is a syntax error in a classic script and legal in a
//! module. `goal` says which goal each source is judged with, and it is decided by the file's
//! NAME -- see `goal` for why the name and not the text.
//!
//! ENGINE ONE -- node, the PRIMARY engine, and the one the house reaches for first. It is the
//! estate's own toolchain, and it reaches the browser's goals EXACTLY and WITHOUT RUNNING
//! anything: `vm.Script` IS the classic-script goal (measured: it refuses a top-level `return`,
//! a top-level `await`, an `import` and an `export`), `vm.compileFunction` is the `CommonJS` goal,
//! and `node --check` on a `.mjs` file is the module goal. See `node`.
//!
//! WHAT `node --check` IS NOT, and why it is not the engine any more. `node --check` on a `.js`
//! file parses it with the goal node reads off the nearest `package.json` -- `CommonJS` or MODULE,
//! never the browser's classic script -- and the `CommonJS` goal ACCEPTS a top-level `return`. So
//! `node --check` is green on a file a browser refuses, and a page that loads it renders empty.
//! That is the hole this engine existed in, and it is closed by asking node a different
//! question.
//!
//! ENGINE TWO -- `JavaScriptCore`, through the `jsc` helper macOS ships, and the FALLBACK: it
//! is the engine that works with nothing installed, because `JavaScriptCore` is in the operating
//! system. It reaches the classic-script goal in TWO probes, because `new Function` builds a
//! function body, and a function body may `return` at its top level where a classic script may
//! not. See `jsc`.
//!
//! WHY NOT `osascript -l JavaScript`, the other route to the same engine: it was measured, and
//! it answers with its EXIT CODE wrong. `osascript` runs the script and reports a failure in its
//! output while exiting 0, so a caller has to read its prose to learn whether the parse failed.
//! `jsc` exits 3 on an uncaught exception and 0 otherwise, so the verdict is a status, not a
//! sentence. Same engine, one that can be asked a question.
//!
//! WHAT THE FALLBACK REPORTS. `new Function` puts the source in a function body, so the error it
//! carries is the wrapper's line, not the file's, and its column is a constant (measured: 19,
//! for every error shape). So the fallback does not report that column at all. For the LINE it
//! finds the first line at which the source stops parsing -- the prefix up to it parses, the
//! prefix including it does not -- which is the line the defect starts on. `node` reports the
//! line its parser gave up on; the two disagree by design, and each names the line it means.

use std::path::{Path, PathBuf};

use super::goal::Goal;

/// One source to judge: the path to report, the file to hand the engine, and the goal to
/// judge it with.
#[derive(Debug, Clone)]
pub struct Check {
    /// The repo-relative path, as the report names it.
    pub path: String,
    /// The file on disk the engine reads.
    pub check_path: PathBuf,
    /// The goal the source is judged with.
    pub goal: Goal,
}

/// What an engine says about one source.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// It parses.
    Clean,
    /// It does not parse: the line, where the engine has one, and why.
    Syntax {
        /// The line the defect is on, when the engine could say.
        line: Option<usize>,
        /// The engine's own words.
        message: String,
    },
    /// This engine cannot measure this source, and says why.
    Unmeasured(String),
}

/// The engine that parses the sources.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Engine {
    /// node.
    Node {
        /// The binary.
        bin: PathBuf,
    },
    /// `JavaScriptCore`, through macOS's `jsc` helper.
    Jsc {
        /// The binary.
        bin: PathBuf,
    },
}

/// Where macOS keeps the `JavaScriptCore` helper, when it is not on PATH.
const JSC_HELPER: &str =
    "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc";

/// The engine this machine has: node if it has one, else `JavaScriptCore`.
///
/// The ORDER is the house's: node first, because it reaches the browser's goals exactly, and
/// `JavaScriptCore` second, because it is in the operating system and needs nothing installed.
#[must_use]
pub fn discover() -> Option<Engine> {
    if let Some(bin) = which("node") {
        return Some(Engine::Node { bin });
    }
    let jsc = which("jsc").or_else(|| {
        let path = PathBuf::from(JSC_HELPER);
        path.is_file().then_some(path)
    })?;
    Some(Engine::Jsc { bin: jsc })
}

/// The first executable named `name` on PATH.
fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|dir| dir.join(name))
        .find(|candidate| candidate.is_file() && is_executable(candidate))
}

/// Can this file be run?
fn is_executable(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::metadata(path).is_ok_and(|m| m.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    {
        path.is_file()
    }
}

impl Engine {
    /// How this engine names itself in the report.
    #[must_use]
    pub fn label(&self) -> String {
        match self {
            Self::Node { bin } => format!("node, the browser's goals ({})", bin.display()),
            Self::Jsc { bin } => format!("JavaScriptCore jsc, the fallback ({})", bin.display()),
        }
    }

    /// Parse every source, and say what each engine made of it.
    ///
    /// # Errors
    ///
    /// Returns a message when the engine could not be run at all: a gate that cannot parse is a
    /// gate that cannot judge, and it says so rather than reporting a clean tree.
    pub fn check_all(&self, checks: &[Check]) -> Result<Vec<Verdict>, String> {
        match self {
            Self::Node { bin } => super::node::check_all(bin, checks),
            Self::Jsc { bin } => super::jsc::check_all(bin, checks),
        }
    }
}

/// A path under the system's temporary directory, named for this process.
#[must_use]
pub fn temp_path(kind: &str) -> PathBuf {
    std::env::temp_dir().join(format!("goh-js-syntax.{}.{kind}", std::process::id()))
}

/// A JavaScript string literal, for a path.
#[must_use]
pub fn js_string(value: &str) -> String {
    let mut out = String::with_capacity(value.len() + 2);
    out.push('"');
    for c in value.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                let _ = std::fmt::Write::write_fmt(&mut out, format_args!("\\u{:04x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

#[cfg(test)]
mod tests {
    use super::{js_string, Engine};

    #[test]
    fn a_path_is_escaped_for_a_javascript_string() {
        assert_eq!(js_string("a\"b\\c"), "\"a\\\"b\\\\c\"");
    }

    #[test]
    fn the_engine_names_itself_by_its_rank() {
        let node = Engine::Node {
            bin: std::path::PathBuf::from("/opt/homebrew/bin/node"),
        };
        assert!(node.label().contains("node"), "{}", node.label());
        let jsc = Engine::Jsc {
            bin: std::path::PathBuf::from("/usr/bin/jsc"),
        };
        assert!(jsc.label().contains("fallback"), "{}", jsc.label());
    }
}
