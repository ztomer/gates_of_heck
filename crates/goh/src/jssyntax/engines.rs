//! The two engines that parse JavaScript, and how they were chosen.
//!
//! ENGINE ONE -- `node --check`. It parses and does not run: node reads the
//! file, hands it to its own parser, and exits. It also knows the goal of a
//! source by itself: a `.mjs` is a module, and a `.js` whose nearest
//! `package.json` says `"type": "module"` is a module, and node re-reads a
//! `.js` that fails as a script as an ES module (measured on node 26.12.0: a
//! file with a top-level `await` passes `node --check` with no `package.json`
//! at all). One spawn per file, and the spawns run side by side, because 43
//! files at 50 ms each is two seconds a reader would wait through.
//!
//! ENGINE TWO -- `JavaScriptCore`, through the `jsc` helper macOS ships. It is
//! the fallback for a machine with no node at all, and it is the engine that
//! works with nothing installed: `JavaScriptCore` is in the operating system.
//! It parses through `new Function(src)`, which builds a function from the
//! source WITHOUT RUNNING IT -- the whole point, because a gate that executes
//! the repository's JavaScript is a gate that runs whatever that repository
//! says to run.
//!
//! WHY NOT `osascript -l JavaScript`, the other route to the same engine: it
//! was measured, and it answers with its EXIT CODE wrong. `osascript` runs the
//! script and reports a failure in its output while exiting 0, so a caller has
//! to read its prose to learn whether the parse failed. `jsc` exits 3 on an
//! uncaught exception and 0 otherwise, so the verdict is a status, not a
//! sentence. Same engine, one that can be asked a question.
//!
//! WHAT THE FALLBACK CANNOT DO. `new Function` builds a classic script body, so
//! a source carrying `import`/`export` is refused by the wrapper rather than
//! by the source. `JavaScriptCore` can parse a module -- `jsc --module-file=F`
//! does -- but it EVALUATES it, and a gate that runs the repository's code is
//! not a gate. So a module source is reported as UNMEASURED under this engine,
//! with the reason, rather than as a syntax error it does not have. `node
//! --check` measures both goals, and it is the engine that measures a module.
//!
//! WHAT THE FALLBACK REPORTS. `new Function` puts the source in a function
//! body, so the error it carries is the wrapper's line, not the file's, and
//! its column is a constant (measured: 19, for every error shape). So the
//! fallback does not report that column at all. For the LINE it finds the
//! first line at which the source stops parsing -- the prefix up to it parses,
//! the prefix including it does not -- which is the line the defect starts
//! on. `node` reports the line its parser gave up on; the two disagree by
//! design, and each names the line it means.

use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::process::Command;

/// One source to judge: the path to report, and the file to hand the engine.
#[derive(Debug, Clone)]
pub struct Check {
    /// The repo-relative path, as the report names it.
    pub path: String,
    /// The file on disk the engine reads.
    pub check_path: PathBuf,
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
    /// `node --check`.
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
            Self::Node { bin } => format!("node --check ({})", bin.display()),
            Self::Jsc { bin } => format!("JavaScriptCore jsc ({})", bin.display()),
        }
    }

    /// Does this engine measure a MODULE source -- one carrying `import` or
    /// `export`?
    ///
    /// `node --check` does: it reads the goal off the file and the nearest
    /// `package.json`. `JavaScriptCore` does not: `new Function` builds a
    /// classic script body, and the shell's module mode EVALUATES what it
    /// parses, which a gate must not do to the repository's code.
    #[must_use]
    pub const fn measures_modules(&self) -> bool {
        matches!(self, Self::Node { .. })
    }

    /// Parse every source, and say what each engine made of it.
    ///
    /// # Errors
    ///
    /// Returns a message when the engine could not be run at all: a gate that
    /// cannot parse is a gate that cannot judge, and it says so rather than
    /// reporting a clean tree.
    pub fn check_all(&self, checks: &[Check]) -> Result<Vec<Verdict>, String> {
        match self {
            Self::Node { bin } => node_all(bin, checks),
            Self::Jsc { bin } => jsc_all(bin, checks),
        }
    }
}

/// `node --check`, one spawn per file, the spawns side by side.
fn node_all(bin: &Path, checks: &[Check]) -> Result<Vec<Verdict>, String> {
    let workers = std::thread::available_parallelism()
        .map_or(1, std::num::NonZero::get)
        .min(8)
        .min(checks.len().max(1));
    let chunk = checks.len().div_ceil(workers);
    let mut out: Vec<Result<Verdict, String>> = Vec::with_capacity(checks.len());
    std::thread::scope(|scope| {
        let handles: Vec<_> = checks
            .chunks(chunk)
            .map(|part| {
                scope.spawn(move || {
                    part.iter()
                        .map(|check| node_one(bin, check))
                        .collect::<Vec<_>>()
                })
            })
            .collect();
        for handle in handles {
            let part = match handle.join() {
                Ok(part) => part,
                // A worker that panicked took the whole run with it: the panic is
                // the news, not a missing verdict.
                Err(payload) => std::panic::resume_unwind(payload),
            };
            out.extend(part);
        }
    });
    let mut verdicts = Vec::with_capacity(out.len());
    for verdict in out {
        verdicts.push(verdict?);
    }
    Ok(verdicts)
}

/// `node --check <file>`: its verdict, and its words when it has one.
fn node_one(bin: &Path, check: &Check) -> Result<Verdict, String> {
    let out = Command::new(bin)
        .arg("--check")
        .arg(&check.check_path)
        .output()
        .map_err(|e| format!("node could not be run: {e}"))?;
    if out.status.success() {
        return Ok(Verdict::Clean);
    }
    let stderr = String::from_utf8_lossy(&out.stdout);
    let stderr = if stderr.trim().is_empty() {
        String::from_utf8_lossy(&out.stderr).to_string()
    } else {
        stderr.to_string()
    };
    Ok(Verdict::Syntax {
        line: node_line(&stderr),
        message: node_message(&stderr),
    })
}

/// The line node names: the `path:LINE` on its first line.
fn node_line(stderr: &str) -> Option<usize> {
    for line in stderr.lines() {
        let tail = line.rsplit_once(':')?;
        if let Ok(number) = tail.1.trim().parse::<usize>() {
            return Some(number);
        }
    }
    None
}

/// The message node names: the first `SomethingError: ...` line.
fn node_message(stderr: &str) -> String {
    for line in stderr.lines() {
        let trimmed = line.trim();
        if let Some((name, message)) = trimmed.split_once(": ") {
            if name.ends_with("Error") && !name.contains(' ') {
                return message.trim().to_owned();
            }
        }
    }
    stderr
        .lines()
        .rev()
        .find(|line| !line.trim().is_empty())
        .unwrap_or("node --check failed")
        .trim()
        .to_owned()
}

/// The `JavaScriptCore` script: parse each source, and say what happened.
///
/// It is the shell's own script, and it is embedded rather than written to
/// disk, so what runs is what this file says it runs. `readFile` reads the
/// source; `new Function` parses it without running it.
const JSC_SCRIPT: &str = r#"
var list = JSON.parse(readFile(LIST_PATH));
function firstBadLine(src) {
    var lines = src.split("\n");
    var limit = lines.length < 400 ? lines.length : 400;
    for (var i = 1; i <= limit; i++) {
        try { new Function(lines.slice(0, i).join("\n")); }
        catch (e) { if (e && e.name === "SyntaxError") return i; }
    }
    return 0;
}
for (var i = 0; i < list.length; i++) {
    var item = list[i];
    var src;
    try { src = readFile(item.check); }
    catch (e) { print("UNMEASURED\t" + item.path + "\t" + e.message); continue; }
    if (src.charCodeAt(0) === 35 && src.charCodeAt(1) === 33) {
        src = "//" + src.slice(2);
    }
    try { new Function(src); print("CLEAN\t" + item.path); }
    catch (e) {
        if (e && e.name === "SyntaxError") {
            print("SYNTAX\t" + item.path + "\t" + firstBadLine(src) + "\t" + e.message);
        } else {
            print("UNMEASURED\t" + item.path + "\t" + e.name + ": " + e.message);
        }
    }
}
print("DONE");
"#;

/// `JavaScriptCore`, one process for every source.
///
/// # Errors
///
/// Returns a message when the engine could not be run, or when it answered
/// with something this reader does not understand: a verdict that cannot be
/// read is not a verdict.
fn jsc_all(bin: &Path, checks: &[Check]) -> Result<Vec<Verdict>, String> {
    let list = serde_json::json!(checks
        .iter()
        .map(|c| serde_json::json!({"path": c.path, "check": c.check_path.display().to_string()}))
        .collect::<Vec<_>>());
    let list_path = temp_path("list");
    std::fs::write(&list_path, list.to_string())
        .map_err(|e| format!("could not write the engine's list: {e}"))?;
    let script = JSC_SCRIPT.replace("LIST_PATH", &js_string(&list_path.display().to_string()));
    let out = Command::new(bin)
        .arg("-e")
        .arg(&script)
        .output()
        .map_err(|e| format!("JavaScriptCore could not be run: {e}"))?;
    let _ = std::fs::remove_file(&list_path);
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    if !stdout.contains("DONE") {
        let stderr = String::from_utf8_lossy(&out.stderr);
        return Err(format!(
            "JavaScriptCore did not finish: {}",
            if stderr.trim().is_empty() {
                format!("exit {}", out.status.code().unwrap_or(-1))
            } else {
                stderr.trim().to_owned()
            }
        ));
    }
    let mut verdicts = Vec::with_capacity(checks.len());
    for line in stdout.lines() {
        let mut fields = line.split('\t');
        let Some(kind) = fields.next() else { continue };
        let Some(path) = fields.next() else { continue };
        match kind {
            "CLEAN" => verdicts.push(Verdict::Clean),
            "SYNTAX" => {
                let line = fields.next().and_then(|n| n.parse::<usize>().ok());
                let message = fields.next().unwrap_or("syntax error").to_owned();
                verdicts.push(Verdict::Syntax { line, message });
            }
            "UNMEASURED" => {
                let why = fields.next().unwrap_or("unmeasurable").to_owned();
                verdicts.push(Verdict::Unmeasured(why));
            }
            _ => {}
        }
        let _ = path;
    }
    if verdicts.len() != checks.len() {
        return Err(format!(
            "JavaScriptCore answered for {} of {} source(s)",
            verdicts.len(),
            checks.len()
        ));
    }
    Ok(verdicts)
}

/// A path under the system's temporary directory, named for this process.
fn temp_path(kind: &str) -> PathBuf {
    std::env::temp_dir().join(format!("goh-js-syntax.{}.{kind}", std::process::id()))
}

/// A JavaScript string literal, for a path.
fn js_string(value: &str) -> String {
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
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

#[cfg(test)]
mod tests {
    use super::{js_string, node_line, node_message};

    #[test]
    fn node_line_reads_the_path_line() {
        assert_eq!(node_line("/tmp/a.js:3\n\nSyntaxError: x\n"), Some(3));
        assert_eq!(node_line("no line here\n"), None);
    }

    #[test]
    fn node_message_reads_the_error() {
        let out = "/tmp/a.js:3\nconst x = ;\n          ^\n\nSyntaxError: Unexpected token ';'\n";
        assert_eq!(node_message(out), "Unexpected token ';'");
    }

    #[test]
    fn a_path_is_escaped_for_a_javascript_string() {
        assert_eq!(js_string("a\"b\\c"), "\"a\\\"b\\\\c\"");
    }
}
