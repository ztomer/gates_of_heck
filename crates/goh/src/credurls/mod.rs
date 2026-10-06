//! No credential in `.git/config` -- Rust port of
//! `checks/check_no_credential_urls.py` and `checks/_credential_config.py`
//! (Phase N1).
//!
//! `check_no_secrets` reads tracked files, and `.git/config` is untracked by
//! definition: a live `gho_` token in `remote.origin.url` read clean. This
//! reads every key git resolves a URL from (`remote.*.url`, `.pushurl`,
//! `url.*.insteadOf`, includes followed) plus `credential.*.helper` and
//! `http.*.extraheader`. A finding names the file, the key and the host, and
//! a FINGERPRINT (`sha256:` + 12 hex of the secret) -- never the value.
//!
//! The URL split is Python's `urlsplit`, reproduced: the measured table of
//! remote-URL shapes in `checks/_credential_urls_probe.py` is the spec, and
//! `tests/test_credential_urls_native_parity.py` runs it through both.

use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::{Map, Value};

mod rules;

use rules::{fingerprint, urlsplit, Rules};

const URL_KEYS: &str = r"^remote\..*url$|^url\..*\.insteadof$";
const CONFIG_KEYS: &str = r"^credential\..*helper$|^http\..*extraheader$";

fn remote_of(key: &str) -> String {
    let Some(name) = key.strip_prefix("remote.") else {
        return key.to_owned();
    };
    for suffix in [".pushurl", ".url"] {
        if let Some(n) = name.strip_suffix(suffix) {
            return n.to_owned();
        }
    }
    name.to_owned()
}

fn which_of(key: &str) -> &'static str {
    let lowered = key.to_lowercase();
    if lowered.ends_with(".pushurl") {
        "pushurl"
    } else if lowered.ends_with(".insteadof") {
        "insteadOf"
    } else {
        "url"
    }
}

fn instead_of_base(key: &str) -> String {
    let after = key.split_once("url.").map_or(key, |(_, a)| a);
    after
        .get(..after.len().saturating_sub(".insteadof".len()))
        .unwrap_or("")
        .to_owned()
}

fn label_of(key: &str) -> String {
    let which = which_of(key);
    if which == "insteadOf" {
        let parts = urlsplit(&instead_of_base(key));
        let netloc = parts
            .netloc
            .rsplit_once('@')
            .map_or(parts.netloc.as_str(), |(_, h)| h);
        let netloc = if netloc.is_empty() {
            parts.netloc.as_str()
        } else {
            netloc
        };
        return format!("{}://{netloc}/  [{which}]", parts.scheme);
    }
    format!("{}.{which}", remote_of(key))
}

fn git(root: &Path, args: &[&str]) -> Result<std::process::Output, String> {
    Command::new("git")
        .args(args)
        .current_dir(root)
        .output()
        .map_err(|e| format!("git: {e}"))
}

fn get_regexp(root: &Path, keys: &str, origin: bool) -> Result<String, String> {
    let mut args = vec!["config"];
    if origin {
        args.push("--show-origin");
    }
    args.extend(["--get-regexp", keys]);
    let out = git(root, &args)?;
    match out.status.code() {
        Some(0 | 1) => Ok(String::from_utf8_lossy(&out.stdout).into_owned()),
        code => {
            let stderr = String::from_utf8_lossy(&out.stderr).trim().to_owned();
            let code = code.map_or_else(|| "a signal".to_owned(), |c| c.to_string());
            Err(if origin {
                format!("`git config --get-regexp {keys}` exited {code}")
            } else {
                format!(
                    "`git config --get-regexp {keys}` exited {code}: {}",
                    if stderr.is_empty() {
                        "no stderr"
                    } else {
                        &stderr
                    }
                )
            })
        }
    }
}

fn read_urls(root: &Path) -> Result<Vec<(String, String)>, String> {
    let mut out = Vec::new();
    for line in crate::mdtext::splitlines(&get_regexp(root, URL_KEYS, false)?) {
        let Some((key, url)) = line.split_once(' ').filter(|(_, u)| !u.is_empty()) else {
            continue;
        };
        if key.to_lowercase().ends_with(".insteadof") {
            out.push((key.to_owned(), instead_of_base(key)));
        }
        out.push((key.to_owned(), url.to_owned()));
    }
    Ok(out)
}

fn realpath(p: &Path) -> PathBuf {
    p.canonicalize()
        .unwrap_or_else(|_| std::path::absolute(p).unwrap_or_else(|_| p.to_path_buf()))
}

fn read_values(root: &Path) -> Result<Vec<(String, String, String)>, String> {
    let mut out = Vec::new();
    for line in crate::mdtext::splitlines(&get_regexp(root, CONFIG_KEYS, true)?) {
        let (origin, rest) = line.split_once('\t').unwrap_or((line, ""));
        let (key, value) = rest.split_once(' ').unwrap_or((rest, ""));
        let path = realpath(&root.join(origin.strip_prefix("file:").unwrap_or(origin)));
        out.push((
            path.to_string_lossy().into_owned(),
            key.to_owned(),
            value.to_owned(),
        ));
    }
    Ok(out)
}

fn root_for(root: Option<&str>) -> Result<PathBuf, String> {
    let cwd = root.map_or_else(
        || std::env::current_dir().unwrap_or_default(),
        PathBuf::from,
    );
    if !git(&cwd, &["rev-parse", "--git-dir"])?.status.success() {
        return Err(
            "not a git repo -- .git/config is this gate's subject and there is none".to_owned(),
        );
    }
    let top = git(&cwd, &["rev-parse", "--show-toplevel"])?;
    if !top.status.success() {
        return Err(format!(
            "Command '['git', 'rev-parse', '--show-toplevel']' returned non-zero exit status {}.",
            top.status.code().unwrap_or(-1)
        ));
    }
    Ok(realpath(Path::new(
        String::from_utf8_lossy(&top.stdout).trim(),
    )))
}

/// One finding, `json.dumps(..., sort_keys=True)`'s key order: the fields
/// are `[file, host, key, kind, label, remote, which, why]`.
fn finding(fields: [String; 8], secret: &str) -> Value {
    let [file, host, key, kind, label, remote, which, why] = fields;
    let mut m = Map::new();
    for (k, v) in [
        ("file", file),
        ("fingerprint", fingerprint(secret)),
        ("host", host),
        ("key", key),
        ("kind", kind),
        ("label", label),
        ("remote", remote),
        ("which", which),
        ("why", why),
    ] {
        m.insert(k.to_owned(), Value::String(v));
    }
    Value::Object(m)
}

fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or("").to_owned()
}

type Lines = Vec<(bool, String)>;

/// Every finding, URL keys first, then helper and header values.
fn collect(
    rules: &Rules,
    repo: &Path,
    urls: &[(String, String)],
    values: &[(String, String, String)],
) -> Vec<Value> {
    let config = repo
        .join(".git")
        .join("config")
        .to_string_lossy()
        .into_owned();
    let mut bad = Vec::new();
    for (key, url) in urls {
        if let Some((kind, host, userinfo, why)) = rules.verdict(url) {
            let fields = [
                config.clone(),
                host,
                key.clone(),
                kind.to_owned(),
                label_of(key),
                remote_of(key),
                which_of(key).to_owned(),
                why.to_owned(),
            ];
            bad.push(finding(fields, &userinfo));
        }
    }
    for (origin, key, value) in values {
        if let Some((kind, secret, why)) = rules.judge(key, value) {
            let label = rules.label(key);
            let which = key.rsplit('.').next().unwrap_or("").to_owned();
            bad.push(finding(
                [
                    origin.clone(),
                    String::new(),
                    label.clone(),
                    kind,
                    label,
                    String::new(),
                    which,
                    why,
                ],
                &secret,
            ));
        }
    }
    bad
}

fn report(bad: &[Value]) -> Lines {
    let mut lines = vec![
        (false, format!("✗ {} credential(s) in git config -- the value is NOT printed;", bad.len())),
        (false, "  rotate it, then move it to a credential helper that reads a keychain, or an SSH remote:".to_owned()),
        (false, "    git remote set-url origin <url-without-userinfo>".to_owned()),
    ];
    for f in bad {
        lines.push((
            false,
            format!(
                "  {} [{}] {}: {} [{}] -- {}",
                s(f, "file"),
                s(f, "label"),
                s(f, "host"),
                s(f, "kind"),
                s(f, "fingerprint"),
                s(f, "why")
            ),
        ));
    }
    lines
}

/// (exit code, [(to stderr?, line)]).
fn run(root: Option<&str>, json: bool) -> (i32, Lines) {
    let fail = |e: String| {
        (
            2,
            vec![
                (true, format!("✗ [no_credential_urls] {e}")),
                (
                    false,
                    "→ This gate's subject is .git/config. Without one there is nothing to judge."
                        .to_owned(),
                ),
            ],
        )
    };
    let rules = match Rules::new() {
        Ok(r) => r,
        Err(e) => return fail(e),
    };
    let (repo, urls, values) =
        match root_for(root).and_then(|r| Ok((read_urls(&r)?, read_values(&r)?, r))) {
            Ok((u, v, r)) => (r, u, v),
            Err(e) => return fail(e),
        };
    let bad = collect(&rules, &repo, &urls, &values);
    if !bad.is_empty() {
        if json {
            return (
                1,
                vec![(false, crate::pyjson::dumps_indent2(&Value::Array(bad)))],
            );
        }
        return (1, report(&bad));
    }
    if json {
        return (0, vec![(false, "[]".to_owned())]);
    }
    let common_out = git(
        &repo,
        &["rev-parse", "--path-format=absolute", "--git-common-dir"],
    )
    .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_owned())
    .unwrap_or_default();
    let common = realpath(&if common_out.is_empty() {
        repo.join(".git")
    } else {
        PathBuf::from(common_out)
    });
    let prefix = format!("{}/", common.display());
    if urls.is_empty() && !values.iter().any(|(o, _, _)| o.starts_with(&prefix)) {
        return (0, vec![(false, format!("✓ [no_credential_urls] not applicable — no remote URL here ({} global value(s) clean)", values.len()))]);
    }
    (
        0,
        vec![(
            false,
            format!(
                "✓ [no_credential_urls] OK — {} remote URL(s), {} helper/header value(s)",
                urls.len(),
                values.len()
            ),
        )],
    )
}

/// `goh credential-urls [--root R] [--json]`, or `--verdict URL` to judge one URL.
#[must_use]
pub fn run_command(root: Option<&str>, json: bool, verdict: Option<&str>) -> i32 {
    if let Some(url) = verdict {
        let Ok(rules) = Rules::new() else {
            return 2;
        };
        let got = rules
            .verdict(url)
            .map(|(k, h, u, _)| serde_json::json!([k, h, fingerprint(&u)]));
        println!("{}", crate::pyjson::dumps(&got.unwrap_or(Value::Null)));
        return 0;
    }
    let (code, lines) = run(root, json);
    for (to_err, line) in lines {
        if to_err {
            eprintln!("{line}");
        } else {
            println!("{line}");
        }
    }
    code
}

/// The structural step; the delegated step's label.
#[must_use]
pub fn step() -> Option<i32> {
    let label = "no credential in a git remote URL";
    let start = crate::step_report::begin(label);
    match run(None, false) {
        (0, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, lines) => {
            let text: String = lines.into_iter().map(|(_, l)| l + "\n").collect();
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported(
                    "crates/goh/src/credurls/mod.rs",
                    "check_no_credential_urls.py",
                ),
                &text,
                start,
            );
            Some(code)
        }
    }
}
