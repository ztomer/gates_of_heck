//! `goh canary`: a step under the ceiling, and a report of what it left running -- the native port
//! of `lib/orphan_canary.py`'s `wrap` (roadmap 4C.3), on `goh step`'s core.
//!
//! The Python's contract, pinned against both by `tests/test_orphan_canary.py`: the step's own exit
//! code wins; a GREEN step that left processes in its group is 125 (a leak is a different finding
//! from a failure); every leaked pid is named with its command line and, when its path is under the
//! repo or `CARGO_TARGET_DIR`, `[under <root>]`; a concurrent gate's wrapper is never a leak;
//! `--snapshot` records the survivors and their start-time stamps as JSON for a red push; `--log`
//! takes the step's output.
//
// `goh canary`: The step wrapper plus the leak report, native (`lib/orphan_canary.py wrap`, same
// contract): the step's code wins, a green step that left processes is 125, each survivor named
// with `[under <root>]`, `--snapshot` JSON, `--log`, output to /dev/null otherwise. `local_ci.sh`
// runs every step under it when the binary resolved. Pinned by `test_orphan_canary.py` (both
// canaries).

use goh_tui::ERR;
use std::collections::BTreeMap;
use std::path::Path;
use std::process::Command;

/// What a leaked pid's command line names when it is a concurrent gate run, not a leak.
const MACHINERY: [&str; 4] = [
    "orphan_canary.py",
    "bounded_run.py",
    "/goh step ",
    "/goh canary ",
];
const LEAK_EXIT: i32 = 125;

/// `goh canary`.
#[must_use]
pub fn run_cli(a: &crate::cli_args::CanaryArgs) -> i32 {
    let Some(bounds) = crate::stepcmd::parse_bound_args(&a.bound, "orphan_canary") else {
        return 2;
    };
    if a.bound.command.is_empty() {
        eprintln!("orphan_canary wrap needs a command: ... wrap [opts] -- CMD");
        return 2;
    }
    let label = if a.bound.label.is_empty() {
        a.bound.command[0].clone()
    } else {
        a.bound.label.clone()
    };
    let span = crate::stepcmd::Span::begin();
    // No --log: the output goes NOWHERE, as the Python's DEVNULL -- never the caller's streams. A
    // leaked child holding an inherited pipe makes its reader wait out the leak (the trap
    // lib/bounded_run.py's `run` names).
    let out = a.log.as_deref().unwrap_or_else(|| Path::new("/dev/null"));
    let (code, left) =
        crate::stepcmd::bounded(&a.bound.command, bounds, a.bound.grace, &label, Some(out));
    crate::stepcmd::record(&label, &span, code);
    let repo = a
        .repo
        .clone()
        .or_else(|| std::env::current_dir().ok())
        .unwrap_or_default();
    if let Some(path) = a.snapshot.as_deref().filter(|_| !left.is_empty()) {
        snapshot(path, &a.bound.command, &left);
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
    goh_tui::outln!(
        "{ERR} [orphan_canary] {} {n_process} outlived the run that started them:",
        ours.len(),
        n_process = goh_tui::noun_as(ours.len(), "process", "processes")
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

/// `<pid> <lstart>` for each survivor still running (one `ps`): the stamp a later reader proves
/// the pid against before it signals it, as `lib/pid_owner.py`'s `stamp_line` (GOH.STRAYSIGNAL2).
fn stamps(survivors: &[i32]) -> Vec<String> {
    if survivors.is_empty() {
        return Vec::new();
    }
    let list: Vec<String> = survivors.iter().map(ToString::to_string).collect();
    Command::new("ps")
        .args(["-o", "pid=,lstart=", "-p", &list.join(",")])
        .stdin(std::process::Stdio::null())
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).into_owned())
        .unwrap_or_default()
        .lines()
        .map(|l| l.split_whitespace().collect::<Vec<_>>().join(" "))
        .filter(|l| !l.is_empty())
        .collect()
}

fn snapshot(path: &Path, step: &[String], survivors: &[i32]) {
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let doc = serde_json::json!({
        "v": 2, "step": step, "survivors": survivors, "stamps": stamps(survivors),
    });
    let text = serde_json::to_string_pretty(&doc).unwrap_or_default();
    if let Err(e) = std::fs::write(path, text) {
        eprintln!("orphan_canary: cannot write {}: {e}", path.display());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// No survivors, no `ps` (GOH.SHIPRED01403): `ps -p ""` is a usage error, never an empty
    /// table. `run_cli` only snapshots a non-empty set, so this arm is reached only here.
    #[test]
    fn no_survivors_stamp_nothing() {
        assert_eq!(stamps(&[]), Vec::<String>::new());
    }

    /// A live survivor is stamped with its own pid first, as `pid_owner.stamp_line` reads it.
    #[test]
    fn a_live_survivor_is_stamped_by_its_pid() {
        let me = i32::try_from(std::process::id()).expect("a pid fits an i32");
        let got = stamps(&[me]);
        assert_eq!(got.len(), 1, "{got:?}");
        assert!(got[0].starts_with(&format!("{me} ")), "{got:?}");
    }
}
