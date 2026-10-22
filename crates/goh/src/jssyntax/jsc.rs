//! The fallback engine: macOS's `JavaScriptCore`, reading each source with the goal the house
//! named.
//!
//! WHY THIS ENGINE IS THE FALLBACK, and what it costs. `JavaScriptCore` is in the operating
//! system, so a machine with nothing installed still has a parser -- that is the whole reason
//! this engine exists. It is the fallback and NOT the primary because it cannot reach the
//! browser's classic-script goal in one probe, and this file says how it reaches it anyway.
//!
//! THE TWO PROBES, and why two. `new Function(src)` builds a FUNCTION BODY. A function body is
//! the browser's classic script in every respect but one: a top-level `return` is legal in a
//! function body and ILLEGAL in a classic script, and that is the exact hole `node --check` fell
//! into (measured: `new Function("return 1;")` is ACCEPTED, and so is `node --check` on the same
//! file). So the first probe is the function body, and the second is the same source in a CLASS
//! STATIC BLOCK -- a context where a `return` is illegal, and where the parser is the same
//! parser. Measured: `new Function("class G { static { return 1; } }")` is refused with "Return
//! statements are only valid inside functions."
//!
//! WHAT THE SECOND PROBE COSTS, stated rather than hidden. A class static block is STRICT code,
//! so the second probe refuses three things a browser's sloppy classic script allows: a `with`
//! statement, an octal literal, and `let` (or another strict reserved word) as a name. When the
//! first probe says the source is fine and the second says it is not, the difference is EITHER a
//! top-level `return` (which the house must report) OR a strict-mode-only construct (which the
//! house must NOT report, because the browser allows it). The two are told apart by the
//! engine's own words: a `return` names a return. Anything else is a source this engine cannot
//! judge -- and a source the engine cannot judge is REFUSED (exit 2), never passed.
//!
//! WHAT THE FALLBACK CANNOT DO AT ALL. A module. `JavaScriptCore` parses a module by RUNNING it
//! (`jsc --module-file=F`), and a gate that runs the repository's code is not a gate. So a module
//! source is reported as UNMEASURED under this engine, with the reason, rather than as a syntax
//! error it does not have.
//!
//! WHAT IT NEVER DOES. It never runs the repository's code: `new Function` builds a function from
//! the source and does not call it, and the class static block is a probe, not a program.

use std::path::Path;
use std::process::Command;

use super::engines::{Check, Verdict};

/// The `JavaScriptCore` script: read each source with the goal it was given.
///
/// It is the shell's own script, and it is embedded rather than written to disk, so what runs is
/// what this file says it runs. `readFile` reads the source; `new Function` parses it without
/// running it.
const JSC_SCRIPT: &str = r#"
var list = JSON.parse(readFile(LIST_PATH));
function sloppy(src) {
    try { new Function(src); return null; }
    catch (e) { return e.message; }
}
function classicGoal(src) {
    try { new Function("class __GohClassicGoal { static {\n" + src + "\n} }"); return null; }
    catch (e) { return e.message; }
}
function firstBadLine(src, probe) {
    var lines = src.split("\n");
    var limit = lines.length < 400 ? lines.length : 400;
    for (var i = 1; i <= limit; i++) {
        if (probe(lines.slice(0, i).join("\n"))) return i;
    }
    return 0;
}
for (var i = 0; i < list.length; i++) {
    var item = list[i];
    if (item.goal === "module") {
        print("UNMEASURED\t" + item.path + "\tJavaScriptCore parses a module only by RUNNING it, and a gate does not run the repository's code");
        continue;
    }
    var src;
    try { src = readFile(item.check); }
    catch (e) { print("UNMEASURED\t" + item.path + "\t" + e.message); continue; }
    if (src.charCodeAt(0) === 35 && src.charCodeAt(1) === 33) {
        src = "//" + src.slice(2);
    }
    var bad = sloppy(src);
    if (bad) {
        print("SYNTAX\t" + item.path + "\t" + firstBadLine(src, sloppy) + "\t" + bad);
        continue;
    }
    if (item.goal === "commonjs") {
        print("CLEAN\t" + item.path);
        continue;
    }
    var goalBad = classicGoal(src);
    if (!goalBad) {
        print("CLEAN\t" + item.path);
        continue;
    }
    if (goalBad.toLowerCase().indexOf("return") >= 0) {
        print("SYNTAX\t" + item.path + "\t" + firstBadLine(src, classicGoal) + "\ta top-level return: a browser's classic script refuses it");
        continue;
    }
    print("UNMEASURED\t" + item.path + "\tthis engine cannot tell a top-level return from a strict-mode-only construct: " + goalBad);
}
print("DONE");
"#;

/// Read every source with `JavaScriptCore`, and say what it made of each.
///
/// # Errors
///
/// Returns a message when the engine could not be run, or when it answered with something this
/// reader does not understand: a verdict that cannot be read is not a verdict.
pub fn check_all(bin: &Path, checks: &[Check]) -> Result<Vec<Verdict>, String> {
    let list = serde_json::json!(checks
        .iter()
        .map(|c| {
            serde_json::json!({
                "path": c.path,
                "check": c.check_path.display().to_string(),
                "goal": c.goal.wire(),
            })
        })
        .collect::<Vec<_>>());
    let list_path = super::engines::temp_path("list");
    std::fs::write(&list_path, list.to_string())
        .map_err(|e| format!("could not write the engine's list: {e}"))?;
    let script = JSC_SCRIPT.replace(
        "LIST_PATH",
        &super::engines::js_string(&list_path.display().to_string()),
    );
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
        let Some(_path) = fields.next() else { continue };
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
