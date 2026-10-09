//! `goh canary`: a step under the ceiling, and a report of what it left running -- the native port
//! of `lib/orphan_canary.py`'s `wrap` (roadmap 4C.3), on `goh step`'s core.
//!
//! The Python's contract, pinned against both by `tests/test_orphan_canary.py`: the step's own exit
//! code wins; a GREEN step that left processes in its group is 125 (a leak is a different finding
//! from a failure); every leaked pid is named with its command line and, when its path is under the
//! repo or `CARGO_TARGET_DIR`, `[under <root>]`; a concurrent gate's wrapper is never a leak;
//! `--snapshot` records the survivors as JSON for a red push; `--log` takes the step's output.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;

/// What a leaked pid's command line names when it is a concurrent gate run, not a leak.
const MACHINERY: [&str; 4] = [
    "orphan_canary.py",
    "bounded_run.py",
    "/goh step ",
    "/goh canary ",
];
const LEAK_EXIT: i32 = 125;

/// `goh canary` flags, grouped.
pub struct Args {
    pub repo: Option<PathBuf>,
    pub log: Option<PathBuf>,
    pub snapshot: Option<PathBuf>,
    pub timeout: String,
    pub grace: u64,
    pub label: String,
    pub command: Vec<String>,
}

/// `goh canary`.
#[must_use]
pub fn run(a: &Args) -> i32 {
    let Some(limit) = crate::stepcmd::ceiling(&a.timeout, "orphan_canary") else {
        return 2;
    };
    if a.command.is_empty() {
        eprintln!("orphan_canary wrap needs a command: ... wrap [opts] -- CMD");
        return 2;
    }
    let label = if a.label.is_empty() {
        a.command[0].clone()
    } else {
        a.label.clone()
    };
    let span = crate::stepcmd::Span::begin();
    // No --log: the output goes NOWHERE, as the Python's DEVNULL -- never the caller's streams. A
    // leaked child holding an inherited pipe makes its reader wait out the leak (the trap
    // lib/bounded_run.py's `run` names).
    let out = a.log.as_deref().unwrap_or_else(|| Path::new("/dev/null"));
    let (code, left) = crate::stepcmd::bounded(&a.command, limit, a.grace, &label, Some(out));
    crate::stepcmd::record(&label, &span, code);
    let repo = a
        .repo
        .clone()
        .or_else(|| std::env::current_dir().ok())
        .unwrap_or_default();
    if let Some(path) = a.snapshot.as_deref().filter(|_| !left.is_empty()) {
        snapshot(path, &a.command, &left);
    }
    let ours = judge(&left, &roots(&repo));
    emit(&ours, &repo);
    if code != 0 {
        code
    } else if ours.is_empty() {
        0
    } else {
        LEAK_EXIT
    }
}

fn roots(repo: &Path) -> Vec<String> {
    let mut out = vec![realpath(repo)];
    if let Some(dir) = std::env::var_os("CARGO_TARGET_DIR").filter(|d| !d.is_empty()) {
        out.push(realpath(Path::new(&dir)));
    }
    out
}

fn realpath(p: &Path) -> String {
    std::fs::canonicalize(p)
        .unwrap_or_else(|_| p.to_path_buf())
        .display()
        .to_string()
}

/// `pid <n> <command line>[   [under <root>]]` for every survivor that is not gate machinery.
fn judge(pids: &[i32], roots: &[String]) -> Vec<String> {
    if pids.is_empty() {
        return Vec::new();
    }
    let list: Vec<String> = pids.iter().map(ToString::to_string).collect();
    let table = Command::new("ps")
        .args(["-o", "pid=,command=", "-p", &list.join(",")])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).into_owned())
        .unwrap_or_default();
    let commands: BTreeMap<String, String> = table
        .lines()
        .filter_map(|l| {
            let l = l.trim_start();
            let (pid, cmd) = l.split_once(char::is_whitespace)?;
            Some((pid.to_owned(), cmd.trim().to_owned()))
        })
        .collect();
    list.iter()
        .filter_map(|pid| {
            let cmd = commands.get(pid)?; // exited before it could be described: not a survivor
            if MACHINERY.iter().any(|m| format!("{cmd} ").contains(m)) {
                return None;
            }
            let under = roots
                .iter()
                .find(|r| !r.is_empty() && cmd.contains(r.as_str()));
            Some(under.map_or_else(
                || format!("pid {pid} {cmd}"),
                |root| format!("pid {pid} {cmd}   [under {root}]"),
            ))
        })
        .collect()
}

fn emit(ours: &[String], repo: &Path) {
    if ours.is_empty() {
        return;
    }
    println!(
        "✗ [orphan_canary] {} process(es) outlived the run that started them:",
        ours.len()
    );
    for line in ours {
        println!("    {}", line.chars().take(240).collect::<String>());
    }
    println!(
        "  Something a test spawned is still running out of {}. A leak",
        repo.display()
    );
    println!(
        "  that only shows up as a LATER hang is a leak with no signal of its own — an orphan"
    );
    println!("  holding a build lock blocks every subsequent run with no output at all. Wrap the");
    println!("  child in a guard that reaps it on a panic (`goh unreaped-spawn` names the");
    println!("  pattern).");
}

fn snapshot(path: &Path, step: &[String], survivors: &[i32]) {
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let doc = serde_json::json!({ "v": 1, "step": step, "survivors": survivors });
    let text = serde_json::to_string_pretty(&doc).unwrap_or_default();
    if let Err(e) = std::fs::write(path, text) {
        eprintln!("orphan_canary: cannot write {}: {e}", path.display());
    }
}
