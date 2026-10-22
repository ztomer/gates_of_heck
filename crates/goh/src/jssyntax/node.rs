//! The primary engine: node, reading each source with the goal the house named.
//!
//! WHY NODE IS THE PRIMARY ENGINE, and why it is the one the house reaches for first. It is
//! the estate's own toolchain, and -- more to the point -- it can reach the browser's goals
//! EXACTLY, from inside one process, without running anything:
//!
//!   * `vm.Script` is the SCRIPT goal: the same goal a browser's `<script src>` is. It is
//!     measured, not assumed: a top-level `return`, a top-level `await`, an `import` statement
//!     and an `export` are all REFUSED by it (node 26.12.1), and a source that carries none of
//!     them parses.
//!   * `vm.compileFunction` is the FUNCTION BODY goal, which is what node's `CommonJS` is: sloppy,
//!     and a top-level `return` is legal in it.
//!   * `node --check` on a `.mjs` file is the MODULE goal: `import`, `export` and a top-level
//!     `await` are legal in it, and a top-level `return` is not.
//!
//! WHAT IT IS NOT. `node --check` on a `.js` file is NOT a route to any of these goals: node
//! reads that file's goal off the nearest `package.json`, and the goal it picks is `CommonJS` or
//! MODULE -- never the browser's classic script. That is the hole this engine exists to close:
//! `node --check` ACCEPTS a file with a top-level `return`, and a browser REFUSES it, and a gate
//! that asks node the way node is usually asked is green on a file no page can load.
//!
//! ONE PROCESS FOR THE BULK. The classic and the `CommonJS` sources are read by ONE node process
//! that walks a list, because one spawn per file is one spawn per file. The module sources are
//! read by `node --check`, one spawn each, because a module goal is a different parser inside
//! node and there are few of them.
//!
//! NOTHING RUNS. `vm.Script` compiles and does not run; `vm.compileFunction` compiles and does
//! not run; `node --check` parses and does not run. A gate that executed the repository's
//! JavaScript would be a gate that runs whatever that repository says to run.

use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::process::Command;

use super::engines::{Check, Verdict};
use super::goal::Goal;

/// The node reader: one process, every source it can judge in one goal-agnostic pass.
///
/// It is the shell's own script, embedded rather than written to disk, so what runs is what
/// this file says it runs.
const NODE_SCRIPT: &str = r"
const fs = require('fs');
const vm = require('vm');
const list = JSON.parse(fs.readFileSync(LIST_PATH, 'utf8'));
for (const item of list) {
    let src;
    try { src = fs.readFileSync(item.check, 'utf8'); }
    catch (e) { console.log('UNMEASURED\t' + item.path + '\t' + e.message); continue; }
    try {
        if (item.goal === 'commonjs') {
            vm.compileFunction(src, [], { filename: item.check });
        } else {
            new vm.Script(src, { filename: item.check });
        }
        console.log('CLEAN\t' + item.path);
    } catch (e) {
        const first = String(e.stack || '').split('\n')[0] || '';
        const m = first.match(/:(\d+)$/);
        const where = m ? m[1] : '';
        const message = String(e.message).split('\n')[0];
        console.log('SYNTAX\t' + item.path + '\t' + where + '\t' + message);
    }
}
console.log('DONE');
";

/// Read every source with node, and say what node made of each.
///
/// # Errors
///
/// Returns a message when node could not be run, or when it answered with something this
/// reader does not understand: a verdict that cannot be read is not a verdict.
pub fn check_all(bin: &Path, checks: &[Check]) -> Result<Vec<Verdict>, String> {
    let mut verdicts: Vec<Option<Verdict>> = vec![None; checks.len()];
    let mut batch: Vec<(usize, &Check)> = Vec::new();
    let mut modules: Vec<(usize, &Check)> = Vec::new();
    for (index, check) in checks.iter().enumerate() {
        match check.goal {
            Goal::Module => modules.push((index, check)),
            Goal::Classic | Goal::CommonJs => batch.push((index, check)),
        }
    }
    if !batch.is_empty() {
        for (index, verdict) in batch_ones(bin, &batch)? {
            verdicts[index] = Some(verdict);
        }
    }
    if !modules.is_empty() {
        for (index, verdict) in module_ones(bin, &modules)? {
            verdicts[index] = Some(verdict);
        }
    }
    let mut out = Vec::with_capacity(checks.len());
    for verdict in verdicts {
        out.push(verdict.ok_or("node answered for no source at all")?);
    }
    Ok(out)
}

/// The classic and `CommonJS` sources, read by ONE node process.
fn batch_ones(bin: &Path, checks: &[(usize, &Check)]) -> Result<Vec<(usize, Verdict)>, String> {
    let list = serde_json::json!(checks
        .iter()
        .map(|(_, c)| {
            serde_json::json!({
                "path": c.path,
                "check": c.check_path.display().to_string(),
                "goal": c.goal.wire(),
            })
        })
        .collect::<Vec<_>>());
    let list_path = temp_path("list");
    std::fs::write(&list_path, list.to_string())
        .map_err(|e| format!("could not write the engine's list: {e}"))?;
    let script = NODE_SCRIPT.replace("LIST_PATH", &js_string(&list_path.display().to_string()));
    let out = Command::new(bin)
        .arg("-e")
        .arg(&script)
        .output()
        .map_err(|e| format!("node could not be run: {e}"))?;
    let _ = std::fs::remove_file(&list_path);
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    if !stdout.contains("DONE") {
        let stderr = String::from_utf8_lossy(&out.stderr);
        return Err(format!(
            "node did not finish: {}",
            if stderr.trim().is_empty() {
                format!("exit {}", out.status.code().unwrap_or(-1))
            } else {
                stderr.trim().to_owned()
            }
        ));
    }
    let mut answers: Vec<(String, Verdict)> = Vec::with_capacity(checks.len());
    for line in stdout.lines() {
        let mut fields = line.split('\t');
        let Some(kind) = fields.next() else { continue };
        let Some(path) = fields.next() else { continue };
        let verdict = match kind {
            "CLEAN" => Verdict::Clean,
            "SYNTAX" => {
                let line = fields.next().and_then(|n| n.parse::<usize>().ok());
                let message = fields.next().unwrap_or("syntax error").to_owned();
                Verdict::Syntax { line, message }
            }
            "UNMEASURED" => {
                let why = fields.next().unwrap_or("unmeasurable").to_owned();
                Verdict::Unmeasured(why)
            }
            _ => continue,
        };
        answers.push((path.to_owned(), verdict));
    }
    if answers.len() != checks.len() {
        return Err(format!(
            "node answered for {} of {} source(s)",
            answers.len(),
            checks.len()
        ));
    }
    let mut out = Vec::with_capacity(checks.len());
    for (index, check) in checks {
        let Some((_, verdict)) = answers.iter().find(|(path, _)| *path == check.path) else {
            return Err(format!("node said nothing about {}", check.path));
        };
        out.push((*index, verdict.clone()));
    }
    Ok(out)
}

/// The module sources, read by `node --check`, one spawn each.
fn module_ones(bin: &Path, checks: &[(usize, &Check)]) -> Result<Vec<(usize, Verdict)>, String> {
    let mut out = Vec::with_capacity(checks.len());
    for (index, check) in checks {
        out.push((*index, module_one(bin, check)?));
    }
    Ok(out)
}

/// `node --check <file>`: the module goal, and its verdict.
fn module_one(bin: &Path, check: &Check) -> Result<Verdict, String> {
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
