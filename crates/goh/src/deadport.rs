//! A test's "unreachable" endpoint is a port the test holds, never a literal in the ephemeral range
//! (`goh dead-port`, GOH.DEADPORTSCAN; the class is EST.DEADPORT).
//!
//! THE CLASS, found in ztools on 2026-11-15: a test called `call_osaurus("http://127.0.0.1:59999",
//! ...)` and expected the connect to fail. 59999 is inside the OS's EPHEMERAL range (macOS
//! 49152-65535, `sysctl net.inet.ip.portrange.first/last`; Linux 32768-60999 by default,
//! `ip_local_port_range`), the range the kernel hands to every outgoing connection and to every
//! server bound to port 0. So on a busy machine the "dead" endpoint is sometimes somebody's
//! listener, or the test's own client connected to itself, the connect succeeds, and the test fails
//! at random: it refused ztools' v0.124.0 pin push.
//!
//! WHAT IS JUDGED: TEST code only, by the house's test-path rule (`crate::unreaped::is_test_path`:
//! a test directory, a `test_x`/`x_test`/`x.test.ts` name, `.bats`), a Swift `XCTest` file (a
//! `...Tests` directory or a `...Tests.swift`/`...Test.swift` name), and in Rust any
//! `#[cfg(test)]` region (`crate::unreaped::guard::rust_region_lines`) of any file. A non-test file
//! is never read: there a localhost port is a service's own, not a dead endpoint. In each line
//! (whole-line comments are not read) a literal port in 32768-65535, the union of both ranges,
//! on a loopback or unspecified host is a finding, in any of three spellings:
//!
//! * `HOST:PORT` (`127.0.0.1:59999`, `localhost:59999`, `[::1]:59999`, `0.0.0.0:59999`, in a URL
//!   or an address string);
//! * a quoted host and a port as the next argument (`("127.0.0.1", 59999)`, `host: "localhost",
//!   port: 59999`);
//! * Rust's typed loopback (`Ipv4Addr::LOCALHOST, 59999`, `[127, 0, 0, 1], 59999`).
//!
//! A port below 32768 is no finding (outside both ranges), nor is a port read from a socket
//! (`f"127.0.0.1:{port}"` carries no literal). A deliberate site says so on its line,
//! `dead-port-ok: <reason>`; the escape with no reason is itself a finding.
//!
//! THE FIX (measured on macOS 2026-11-15 by this lane and by ztools 19ed47d,
//! `rust/src/dead_endpoint.rs` `DeadEndpoint::hold`): a socket bound and NOT listening is no dead
//! endpoint on macOS -- it DROPS the SYN, so a connect waits out its whole timeout (Linux refuses
//! it). What refuses at once on both and keeps the port the test's own: open a listener on
//! `127.0.0.1:0` WITHOUT `SO_REUSEADDR`, connect to it, accept, close the listener and hold the
//! connected pair for the test's life. A connect to that port is refused immediately and a plain
//! bind gets `EADDRINUSE` (a server that sets `SO_REUSEADDR` for that exact port can still take it
//! on macOS).
//!
//! WIRING: a structural step in EVERY repo, judged from its own grace's end, `OVER_FROM`
//! (2026-12-07: goh's ships were frozen the day it landed, so the consumers' week counts from a
//! ship days away); before it the scan reads (the floor counts it) and judges nothing. The sites a
//! consumer keeps are seeded in `dead_port_seed.json` (`crate::estate_seed`): seeded files warn,
//! anything else is red.

use crate::estate_seed::{self, Kind, Report, Sites};
use regex::Regex;
use std::path::Path;
use std::sync::LazyLock;

const TAG: &str = "[dead_port]";
/// The step's label, the same line as in `structural_steps.txt`.
pub const LABEL: &str = "no test endpoint on a literal ephemeral port";
/// The escape a deliberate site carries on its line, with its reason.
pub const ESCAPE: &str = "dead-port-ok:";
/// The lowest port either OS may hand out: Linux's default `ip_local_port_range` starts here,
/// macOS's at 49152; both end at 65535.
pub const EPHEMERAL_FIRST: u32 = 32_768;
/// 2026-12-07T00:00:00Z, seconds since the epoch: this scanner's own grace end
/// (`crate::grace::over_from`).
pub const OVER_FROM: u64 = 1_796_601_600;

/// The seed of the sites a consumer keeps.
pub const KIND: Kind = Kind {
    file: "dead_port_seed.json",
    unit: "dead-port site",
    name: "dead-port seed",
};

const FIX: &str = "    A port in 32768-65535 is in the OS's ephemeral range (macOS 49152-65535, Linux\n    \
     32768-60999): any outgoing connection, or any server bound to port 0, may be handed it, so the\n    \
     \"dead\" endpoint answers and the test fails at random (ztools, EST.DEADPORT). Make the port\n    \
     the test's own, held for its life: listen on 127.0.0.1:0 WITHOUT SO_REUSEADDR, connect to it,\n    \
     accept, close the listener and hold the connected pair (ztools rust/src/dead_endpoint.rs,\n    \
     DeadEndpoint::hold); a connect to its port is refused at once and a bind gets EADDRINUSE.\n    \
     Not a socket bound and never listening: macOS drops the SYN and the connect waits out its\n    \
     timeout. Deliberate: `dead-port-ok: <reason>` on the line.\n";

fn rx(p: &str) -> Regex {
    Regex::new(p).unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
}

static COLON: LazyLock<Regex> =
    LazyLock::new(|| rx(r"(?:\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0)|\[::1\]):(\d+)\b"));
static ARG: LazyLock<Regex> = LazyLock::new(|| {
    rx(
        r#"["'](?:localhost|127\.0\.0\.1|::1|0\.0\.0\.0)["']\s*\)?\s*,\s*(?:port\s*[:=]\s*)?(\d+)\b"#,
    )
});
static TYPED: LazyLock<Regex> = LazyLock::new(|| {
    rx(
        r"(?:Ipv[46]Addr::(?:LOCALHOST|UNSPECIFIED)|\[\s*127\s*,\s*0\s*,\s*0\s*,\s*1\s*\])\s*\)?\s*,\s*(\d+)\b",
    )
});
static TEST_NAME: LazyLock<Regex> = LazyLock::new(|| rx(crate::unreaped::TEST_NAME));

/// The extensions read: every source language a test is written in (the patterns are
/// language-agnostic), plus an extensionless script with a shebang.
const EXTS: [&str; 25] = [
    "rs", "py", "sh", "bash", "zsh", "bats", "swift", "ts", "tsx", "js", "jsx", "mjs", "cjs", "go",
    "kt", "kts", "java", "rb", "c", "cc", "cpp", "m", "mm", "zig", "lua",
];

/// Why one line names a dead endpoint on an ephemeral port: the ports, or the bare escape.
#[must_use]
pub fn line_finding(line: &str) -> Option<String> {
    let t = line.trim_start();
    if ["#", "//", "/*", "*"].iter().any(|c| t.starts_with(c)) {
        return None;
    }
    if let Some(at) = line.find(ESCAPE) {
        let reason = line[at + ESCAPE.len()..].trim();
        return reason
            .is_empty()
            .then(|| format!("`{ESCAPE}` names no reason"));
    }
    let mut ports: Vec<u32> = Vec::new();
    for re in [&*COLON, &*ARG, &*TYPED] {
        for c in re.captures_iter(line) {
            let Some(p) = c.get(1).and_then(|m| m.as_str().parse::<u32>().ok()) else {
                continue;
            };
            if (EPHEMERAL_FIRST..=65_535).contains(&p) && !ports.contains(&p) {
                ports.push(p);
            }
        }
    }
    if ports.is_empty() {
        return None;
    }
    let list = ports
        .iter()
        .map(u32::to_string)
        .collect::<Vec<_>>()
        .join(", ");
    Some(format!(
        "port {list} is in the ephemeral range: another connection can be handed it"
    ))
}

/// A test source by its path: the house's rule plus Swift's `XCTest` layout.
#[must_use]
pub fn is_test_path(rel: &str) -> bool {
    if crate::unreaped::is_test_path(rel, &TEST_NAME) {
        return true;
    }
    let mut parts: Vec<&str> = rel.split('/').collect();
    let name = parts.pop().unwrap_or(rel);
    parts.iter().any(|p| p.ends_with("Tests"))
        || name.ends_with("Tests.swift")
        || name.ends_with("Test.swift")
}

fn ext_of(rel: &str) -> &str {
    let name = rel.rsplit('/').next().unwrap_or(rel);
    name.rsplit_once('.').map_or("", |(_, e)| e)
}

/// `(line, why)` of every finding in one file's text, judged as test code.
///
/// The whole file when its path is a test's, else only a Rust file's `#[cfg(test)]` regions.
/// `None` when the file holds no test code at all (it is not read, and not counted).
#[must_use]
pub fn scan(rel: &str, text: &str) -> Option<Vec<(usize, String)>> {
    let ext = ext_of(rel);
    let script = ext.is_empty() && text.starts_with("#!");
    if !(script || EXTS.contains(&ext)) {
        return None;
    }
    let region = if is_test_path(rel) {
        None
    } else if ext == "rs" {
        let masked = crate::unreaped::mask::mask_rust(text);
        Some(crate::unreaped::guard::rust_region_lines(&masked)?)
    } else {
        return None;
    };
    let in_test = |i: usize| {
        region
            .as_ref()
            .is_none_or(|r| r.get(i).copied().unwrap_or(false))
    };
    Some(
        text.split('\n')
            .enumerate()
            .filter(|(i, _)| in_test(*i))
            .filter_map(|(i, l)| line_finding(l).map(|why| (i + 1, why)))
            .collect(),
    )
}

fn sites(root: &Path, files: &[String], exclude: &str, staged: bool) -> Result<Sites, String> {
    let exclude = crate::steps::compile_exclude(exclude)?;
    let mut read = 0usize;
    let mut out = Sites::new();
    for rel in files {
        let ext = ext_of(rel);
        if !(EXTS.contains(&ext) || ext.is_empty())
            || exclude.as_ref().is_some_and(|x| x.is_match(rel))
        {
            continue;
        }
        let Some(blob) = crate::gitutil::content_bytes(root, rel, staged) else {
            continue;
        };
        if blob[..blob.len().min(8000)].contains(&0) {
            continue;
        }
        let text = String::from_utf8_lossy(&blob);
        let Some(hits) = scan(rel, &text) else {
            continue;
        };
        read += 1;
        let rows: Vec<&str> = text.split('\n').collect();
        let found: Vec<String> = hits
            .into_iter()
            .map(|(n, why)| {
                let line = rows.get(n - 1).copied().unwrap_or("");
                format!("    {rel}:{n}: {}  -- {why}", estate_seed::clip(line))
            })
            .collect();
        if !found.is_empty() {
            out.insert(rel.clone(), found);
        }
    }
    crate::subject::record(&crate::subject::DEAD_PORT, read);
    Ok(out)
}

/// The verdict over `files`: `(exit code, stdout, stderr)`.
fn judge(root: &Path, files: &[String], exclude: &str, staged: bool) -> (i32, String, String) {
    use goh_tui::ERR;
    if !crate::grace::over_from(OVER_FROM) {
        // The scan is made (the floor counts it) and never judged, and the seed never loaded for
        // a verdict, so no entry written within the grace reads as stale (GOH.GRACEEND). What the
        // day will find is the warning (GOH.GRACESHAPES).
        let day = sites(root, files, exclude, staged);
        return crate::grace::ahead::warn_only((root, staged), (KIND, TAG), day);
    }
    let seed = match estate_seed::load(root, KIND, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let found = match sites(root, files, exclude, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let report = Report {
        tag: TAG,
        item: "GOH.DEADPORTSCAN",
        what: (
            "test endpoint on a literal ephemeral port",
            "test endpoints on a literal ephemeral port",
        ),
        fix: FIX,
        ok: "no test names a dead endpoint on a literal ephemeral port".to_string(),
    };
    estate_seed::verdict(root, staged, KIND, &seed, &found, &report)
}

/// `goh dead-port [--staged] [--exclude RE]`.
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
        "crates/goh/src/deadport.rs",
        judge(repo, files, &cfg.exclude, staged),
    )
}

/// The lines `goh dead-port` judges in one file: its findings, through its own selection.
#[must_use]
pub fn judged_lines(rel: &str, text: &str) -> Vec<usize> {
    scan(rel, text)
        .unwrap_or_default()
        .into_iter()
        .map(|(n, _)| n)
        .collect()
}

/// This scanner's row in the shape table (`crate::shapes::table::ROWS`, GOH.CLASS.SHAPE).
///
/// The probe is a loopback host and a port in 32768-65535 in any of the three spellings, with
/// none of the scanner's test-code selection.
pub const SHAPES: crate::shapes::Declared = {
    use crate::shapes::{hooks, Declared, Shape, Token, Verdict};
    Declared {
        probe: r#"(?:(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):|["'](?:localhost|127\.0\.0\.1|::1|0\.0\.0\.0)["']\s*\)?\s*,\s*(?:port\s*[:=]\s*)?|(?:LOCALHOST|UNSPECIFIED|\[\s*127\s*,\s*0\s*,\s*0\s*,\s*1\s*\])\s*\)?\s*,\s*)(?:3276[89]|327[7-9]\d|32[89]\d\d|3[3-9]\d{3}|[45]\d{4}|6[0-4]\d{3}|65[0-4]\d\d|655[0-2]\d|6553[0-5])\b"#,
        judged: hooks::dead_port,
        owns: &[Token {
            name: "a literal ephemeral port on a loopback host",
            sample: ("tests/test_a.py", "u = \"http://127.0.0.1:59999\"\n"),
        }],
        sources: &[
            "crates/goh/src/deadport.rs",
            "crates/goh/src/unreaped/mod.rs",
            "crates/goh/src/unreaped/guard.rs",
        ],
        opt_in: None,
        shapes: &[
            Shape {
                name: "HOST:PORT in a test file",
                path: r"(?i)(^|/)tests?/|(^|/)test_|_test\.|\.test\.",
                line: r":\d",
                verdict: Verdict::Covered,
                fixture: ("tests/test_a.py", "u = \"http://127.0.0.1:59999/v1\"\n"),
            },
            Shape {
                name: "a quoted host and the port as the next argument",
                path: r"(?i)(^|/)tests?/",
                line: r",",
                verdict: Verdict::Covered,
                fixture: ("tests/test_b.py", "s.connect((\"127.0.0.1\", 59999))\n"),
            },
            Shape {
                name: "Rust's typed loopback in a #[cfg(test)] region",
                path: r"(?i)\.rs$",
                line: r"LOCALHOST|127\s*,",
                verdict: Verdict::Covered,
                fixture: (
                    "src/a.rs",
                    "#[cfg(test)]\nmod tests {\n    fn t() { let a = (Ipv4Addr::LOCALHOST, 59999); }\n}\n",
                ),
            },
            Shape {
                name: "a Swift XCTest file",
                path: r"Tests(/|\.swift$)",
                line: "",
                verdict: Verdict::Covered,
                fixture: ("Tests/AppTests/ClientTests.swift", "let u = \"http://localhost:59999\"\n"),
            },
            Shape {
                name: "a TypeScript or JavaScript test",
                path: r"(?i)\.(test|spec)\.[cm]?[jt]sx?$|(^|/)__tests__/",
                line: "",
                verdict: Verdict::Covered,
                fixture: ("web/a.test.ts", "const u = 'http://[::1]:59999';\n"),
            },
            Shape {
                name: "an extensionless test script with a shebang",
                path: r"(^|/)tests?/[^./]+$",
                line: "",
                verdict: Verdict::Covered,
                fixture: ("tests/run", "#!/bin/bash\ncurl -s http://127.0.0.1:59999/health\n"),
            },
            Shape {
                name: "a source outside test code",
                path: r"(?i)\.(rs|py|sh|bash|zsh|swift|[cm]?[jt]sx?|go|kts?|java|rb|c|cc|cpp|mm?|zig|lua)$",
                line: "",
                verdict: Verdict::OutOfScope("there a loopback port is a service's own, not a dead endpoint"),
                fixture: ("src/server.py", "BASE = \"http://127.0.0.1:59999\"\n"),
            },
            Shape {
                name: "a Rust file's code outside #[cfg(test)]",
                path: r"(?i)\.rs$",
                line: "",
                verdict: Verdict::OutOfScope("above the test module a port is the service's own"),
                fixture: (
                    "src/b.rs",
                    "const BASE: &str = \"http://127.0.0.1:59999\";\n#[cfg(test)]\nmod tests;\nfn f() {}\n",
                ),
            },
            Shape {
                name: "a whole-line comment",
                path: "",
                line: r"^\s*(#|//|/\*|\*)",
                verdict: Verdict::OutOfScope("a comment naming the port connects to nothing"),
                fixture: ("tests/test_c.py", "# was http://127.0.0.1:59999\n"),
            },
            Shape {
                name: "a reasoned escape",
                path: "",
                line: r"dead-port-ok:\s*\S",
                verdict: Verdict::OutOfScope("`dead-port-ok: <reason>` is the decision, recorded"),
                fixture: (
                    "tests/test_d.py",
                    "u = \"http://127.0.0.1:59999\"  # dead-port-ok: the fixture server listens here\n",
                ),
            },
            Shape {
                name: "a data or prose file",
                path: r"(?i)\.(md|markdown|txt|rst|html?|ya?ml|toml|json|cfg|ini|conf|env|plist|xml|csv)$",
                line: "",
                verdict: Verdict::OutOfScope("data and prose run no test; a test that reads one names the port in code"),
                fixture: ("tests/fixtures/a.yml", "url: http://127.0.0.1:59999\n"),
            },
        ],
    }
};

#[cfg(test)]
mod tests;
