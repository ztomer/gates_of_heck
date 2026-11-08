//! Shell lint -- Rust port of the retired `checks/check_shell_lint.sh` (Phase N1).
//!
//! Every tracked `*.sh`, and every extensionless `hooks/*` file (at `--staged`, the staged blobs,
//! checked out of the index into a temp dir) must pass `bash -n`, and the
//! syntactically clean ones must pass `shellcheck -x` -- one call per file, following what it
//! sources, in the tree `crate::shell_lint_scope` names (GOH.SHX). An
//! ERROR always fails. Below it, down to `.gatesrc`'s `GOH_SHELL_LINT_SEVERITY` (default
//! `warning`), a finding is debt held by `shell_lint_seed.json` (`crate::shell_lint_seed`): it
//! may only fall, and a stale entry fails. A missing shellcheck is a refusal, not a pass. Every
//! spawn runs under the step ceiling (`crate::bounded`).

use crate::shell_lint_scope::Files;
use goh_tui::{ERR, OK};
use std::path::{Path, PathBuf};
use std::process::Command;

type Lines = Vec<(bool, String)>;

fn bounded(cmd: &mut Command) -> Result<crate::bounded::Ran, String> {
    let ceiling = crate::bounded::step_ceiling();
    let ran = crate::bounded::run(cmd, None, ceiling).map_err(|e| e.to_string())?;
    if ran.timed_out {
        return Err(format!("TIMED OUT after {}s", ceiling.as_secs()));
    }
    Ok(ran)
}

/// One file's verdict: its `bash -n` failure line, or its shellcheck lines
/// (display paths already in place). What the cache stores.
#[derive(Clone, Default)]
struct Verdict {
    syntax: Option<String>,
    lines: Vec<String>,
}

impl Verdict {
    fn to_json(&self) -> serde_json::Value {
        serde_json::json!({ "syntax": self.syntax, "lines": self.lines })
    }

    fn from_json(v: &serde_json::Value) -> Option<Self> {
        let syntax = match v.get("syntax")? {
            serde_json::Value::Null => None,
            s => Some(s.as_str()?.to_owned()),
        };
        let lines = v
            .get("lines")?
            .as_array()?
            .iter()
            .map(|l| l.as_str().map(str::to_owned))
            .collect::<Option<_>>()?;
        Some(Self { syntax, lines })
    }
}

/// The OLDEST shellcheck this gate accepts: `gates/required_tools.tsv`'s floor, the version the
/// gate is calibrated against (`tests/test_check_shell_lint.py` pins the two equal). Enforced HERE,
/// not only by `required_tools.py --check`: a too-old shellcheck passed this gate (G2c).
const SHELLCHECK_FLOOR: &str = "0.11.0";

/// `Ok` when PATH's shellcheck is at or above the floor; the refusal, naming both, otherwise.
fn at_floor() -> Result<(), String> {
    let ran = bounded(Command::new("shellcheck").arg("--version"))
        .map_err(|e| format!("shellcheck --version: {e}"))?;
    let text = String::from_utf8_lossy(&ran.stdout);
    let Some(have) = crate::shellcheck_version::reported_version(&text) else {
        return Err(format!(
            "shellcheck --version reported no version, and this gate needs {SHELLCHECK_FLOOR} or newer"
        ));
    };
    if crate::shellcheck_version::newer_or_equal(have, SHELLCHECK_FLOOR) {
        return Ok(());
    }
    Err(format!(
        "shellcheck {have} is below this gate's floor {SHELLCHECK_FLOOR} -- brew upgrade shellcheck"
    ))
}

/// The first executable named `tool` on `PATH` -- the one `Command` runs.
fn which(tool: &str) -> Option<PathBuf> {
    std::env::split_paths(&std::env::var_os("PATH")?)
        .map(|d| d.join(tool))
        .find(|p| p.is_file())
}

/// Everything a verdict depends on besides the file: both tools' identities,
/// the flags, `SHELLCHECK_OPTS`, and the user-level rc files.
fn tools_key(severity: &str) -> String {
    let id = |t: &str| {
        which(t).map_or_else(
            || format!("{t}:-"),
            |p| crate::verdict_cache::file_identity(&p),
        )
    };
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default();
    let xdg =
        std::env::var_os("XDG_CONFIG_HOME").map_or_else(|| home.join(".config"), PathBuf::from);
    format!(
        "shell-lint v4|{}|{}|-x --severity={severity} --format=gcc|opts={}|{}|{}",
        id("bash"),
        id("shellcheck"),
        std::env::var("SHELLCHECK_OPTS").unwrap_or_default(),
        rc_identity(&home.join(".shellcheckrc")),
        rc_identity(&xdg.join("shellcheckrc")),
    )
}

fn rc_identity(path: &Path) -> String {
    std::fs::read(path).map_or_else(
        |_| "-".to_owned(),
        |b| crate::verdict_cache::content_hash(&b),
    )
}

/// The `.shellcheckrc` files shellcheck would find for `src`: its directory and every parent.
fn dir_rc_key(src: &Path, memo: &mut std::collections::HashMap<PathBuf, String>) -> String {
    let Some(dir) = src.parent() else {
        return String::new();
    };
    if let Some(hit) = memo.get(dir) {
        return hit.clone();
    }
    let key: Vec<String> = dir
        .ancestors()
        .filter_map(|d| {
            let rc = d.join(".shellcheckrc");
            rc.is_file()
                .then(|| format!("{}={}", rc.display(), rc_identity(&rc)))
        })
        .collect();
    let key = key.join(";");
    memo.insert(dir.to_path_buf(), key.clone());
    key
}

/// `bash -n` and shellcheck for the files no record covers; the verdicts, in order.
fn check_misses(
    misses: &[&(String, PathBuf)],
    severity: &str,
    base: &Path,
) -> Result<(Vec<Verdict>, bool), String> {
    let mut out: Vec<Verdict> = vec![Verdict::default(); misses.len()];
    let mut clean: Vec<usize> = Vec::new();
    for (i, entry) in misses.iter().enumerate() {
        let ran = bounded(Command::new("bash").arg("-n").arg(&entry.1))
            .map_err(|e| format!("bash -n {}: {e}", entry.0))?;
        if ran.code == Some(0) {
            clean.push(i);
        } else {
            let err = String::from_utf8_lossy(&ran.stderr);
            out[i].syntax = Some(format!(
                "{}: bash -n: {}",
                entry.0,
                err.lines().next().unwrap_or("")
            ));
        }
    }
    if clean.is_empty() {
        return Ok((out, true));
    }
    // ONE shellcheck per file, with `-x`, in `base`. Given several files at once, shellcheck
    // follows a `source` of another file in the batch, so a finding (SC2128, SC2154, SC2034 ...)
    // depended on which files shared the call and the seed flapped (GOH.SHSEV). One call per
    // file WITHOUT `-x` followed nothing, and the lint went blind to every finding that needs a
    // sourced file (GOH.SHX). With `-x`, a file reads what it sources whoever shares the call.
    let mut attributable = true;
    for &i in &clean {
        let mut cmd = Command::new("shellcheck");
        cmd.current_dir(base)
            .arg("-x")
            .arg(format!("--severity={severity}"))
            .arg("--format=gcc")
            .arg(&misses[i].1);
        let ran = bounded(&mut cmd).map_err(|e| format!("shellcheck: {e}"))?;
        let mut text = String::from_utf8_lossy(&ran.stdout).into_owned();
        text.push_str(&String::from_utf8_lossy(&ran.stderr));
        let src = misses[i].1.to_string_lossy();
        for line in text
            .trim_end_matches('\n')
            .split('\n')
            .filter(|l| !l.is_empty())
        {
            if line.starts_with(&format!("{src}:")) {
                out[i].lines.push(line.replace(&*src, &misses[i].0));
            } else {
                // A line no file owns (a tool error): a verdict that cannot be attributed
                // cannot be reused, so this run records nothing.
                attributable = false;
                out[i].lines.push(line.to_owned());
            }
        }
    }
    Ok((out, attributable))
}

/// What the tools said: the lines that always fail (a syntax error, a shellcheck ERROR, a tool
/// error) and the findings below error, grouped `file#code` for the seed.
#[derive(Default)]
struct Judged {
    fail: Vec<String>,
    failed: Vec<String>,
    found: std::collections::BTreeMap<String, Vec<crate::shell_lint_seed::Finding>>,
}

/// Every file's verdict, from the cache where a record's key matches, from the
/// tools otherwise: `(failure lines, files that failed)` in the reference's order.
fn judge(scope: &crate::shell_lint_scope::Scope, severity: &str) -> Result<Judged, String> {
    let files = &scope.files;
    let cache = crate::verdict_cache::Cache::open("shell-lint");
    let tools = cache
        .as_ref()
        .map(|_| tools_key(severity))
        .unwrap_or_default();
    let mut memo = std::collections::HashMap::new();
    let keys: Vec<Option<String>> = files
        .iter()
        .map(|(disp, src)| {
            cache.as_ref()?;
            let deps = scope.deps.key(disp)?;
            Some(format!(
                "{tools}|rc={}|{disp}|{deps}",
                dir_rc_key(src, &mut memo)
            ))
        })
        .collect();
    let mut verdicts: Vec<Option<Verdict>> = keys
        .iter()
        .map(|k| {
            cache
                .as_ref()
                .zip(k.as_ref())
                .and_then(|(c, k)| c.get(k))
                .and_then(|v| Verdict::from_json(&v))
        })
        .collect();
    let misses: Vec<usize> = (0..files.len())
        .filter(|i| verdicts[*i].is_none())
        .collect();
    if !misses.is_empty() {
        let entries: Vec<&(String, PathBuf)> = misses.iter().map(|i| &files[*i]).collect();
        let (fresh, attributable) = check_misses(&entries, severity, &scope.base)?;
        for (i, v) in misses.iter().zip(fresh) {
            if let (Some(c), Some(k), true) = (cache.as_ref(), keys[*i].as_ref(), attributable) {
                c.put(k, &v.to_json());
            }
            verdicts[*i] = Some(v);
        }
    }
    let verdicts: Vec<Verdict> = verdicts
        .into_iter()
        .map(Option::unwrap_or_default)
        .collect();
    let mut out = Judged::default();
    let mark = |failed: &mut Vec<String>, name: &str| {
        if !failed.iter().any(|f| f == name) {
            failed.push(name.to_owned());
        }
    };
    for v in &verdicts {
        if let Some(line) = &v.syntax {
            out.fail.push(line.clone());
            mark(
                &mut out.failed,
                line.split(": bash -n:").next().unwrap_or(line),
            );
        }
    }
    for ((disp, _), v) in files.iter().zip(&verdicts) {
        for line in &v.lines {
            if let Some((rest, code)) = crate::shell_lint_seed::finding_of(line, disp) {
                crate::shell_lint_seed::add(&mut out.found, disp, code, rest);
            } else {
                out.fail.push(line.clone());
                mark(&mut out.failed, line.split(':').next().unwrap_or(line));
            }
        }
    }
    Ok(out)
}

fn run(staged: bool, exclude: &str, seed: bool) -> (i32, Lines) {
    let exclude = match crate::steps::compile_exclude(exclude) {
        Ok(x) => x,
        Err(m) => return (2, vec![(true, format!("{ERR} [shell_lint] {m}"))]),
    };
    let Some(root) = crate::gitutil::repo_root().map(PathBuf::from) else {
        return (
            0,
            vec![(false, "[shell_lint] not a git repo — skipping".to_owned())],
        );
    };
    let (severity, floor) = match crate::shell_lint_seed::config(&root, staged, seed) {
        Ok(got) => got,
        Err(m) => return (2, vec![(true, format!("{ERR} [shell_lint] {m}"))]),
    };
    let scope = match crate::shell_lint_scope::gather(&root, staged, exclude.as_ref()) {
        Ok(got) => got,
        Err(e) => return (2, vec![(true, format!("{ERR} [shell_lint] {e}"))]),
    };
    let files: &Files = &scope.files;
    crate::subject::record(&crate::subject::SHELL_LINT, files.len());
    // Over an empty scope only a staged run is done: a full run still owes the seed its stale
    // entries and the floor its verdict (GOH.REV2), and neither needs shellcheck.
    if files.is_empty() && !staged {
        let none = std::collections::BTreeMap::new();
        return crate::shell_lint_seed::conclude(&root, &[], none, false, seed, floor);
    }
    if files.is_empty() {
        return (
            0,
            vec![(
                false,
                format!("{OK} [shell_lint] OK — no shell files in scope"),
            )],
        );
    }
    if !crate::pyformat::on_path("shellcheck") {
        return (1, vec![(true, format!("{ERR} [shell_lint] shellcheck is not installed, and this gate does not pass without it -- brew install shellcheck"))]);
    }
    if let Err(why) = at_floor() {
        return (1, vec![(true, format!("{ERR} [shell_lint] {why}"))]);
    }
    let verdict = judge(&scope, severity);
    let judged = match verdict {
        Ok(v) => v,
        Err(e) => return (1, vec![(true, format!("{ERR} [shell_lint] {e}"))]),
    };
    let scope = if staged { "staged" } else { "tracked" };
    if !judged.failed.is_empty() {
        let mut lines = vec![(
            true,
            format!(
                "{ERR} [shell_lint] {} file(s) failed ({scope}):",
                judged.failed.len()
            ),
        )];
        lines.extend(judged.fail.into_iter().map(|f| (true, format!("  {f}"))));
        return (1, lines);
    }
    let paths: Vec<String> = files.iter().map(|(d, _)| d.clone()).collect();
    crate::shell_lint_seed::conclude(&root, &paths, judged.found, staged, seed, floor)
}

/// `goh shell-lint [--staged] [--seed] [--exclude RE]`.
#[must_use]
pub fn run_command(staged: bool, exclude: &str, seed: bool) -> i32 {
    let (code, lines) = run(staged, exclude, seed);
    for (to_err, line) in lines {
        if to_err {
            goh_tui::eoutln(&line);
        } else {
            goh_tui::outln(&line);
        }
    }
    code
}

/// The structural step; the delegated step's labels.
#[must_use]
pub fn step(cfg: &crate::gatesrc::Gatesrc, staged: bool) -> Option<i32> {
    let label = if staged {
        "shell lint (staged)"
    } else {
        "shell lint"
    };
    let start = crate::step_report::begin(label);
    match run(staged, &cfg.exclude, false) {
        (0, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, lines) => {
            let text: String = lines.into_iter().map(|(_, l)| l + "\n").collect();
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/shell_lint.rs"),
                &text,
                start,
            );
            Some(code)
        }
    }
}
