//! `goh bare-open` over Python: a subprocess that runs `open` (GOH.BAREOPEN2).
//!
//! The shell scan reads shell sources; a Python tool that runs `open` through `subprocess` hands the
//! launched app the same caller environment, and was invisible to it. Two shapes, read off the
//! token stream (`crate::pylex`, so a comment or a docstring is never read):
//!
//! * an argv list whose command word is `open` or `/usr/bin/open`: `["open", "-a", app]`;
//! * a command string handed to a process call (`run`, `call`, `Popen`, `system`, ...): `"open -a
//!   Foo"`, `f"/usr/bin/open {app}"`, `"cd x && open -g Y"`. A string is judged by the shell scan
//!   (`crate::bareopen::scan_line`) AND must put an option, a path or a variable after `open`; a
//!   string anywhere else (a message, a docstring, an assert, shell text a test writes to a file)
//!   launches nothing and is prose. An argv list needs a second element, so `== ["open"]` is not one.
//!
//! What it cannot see is the environment the call passes: `env=clean` reads the same as no `env`.
//! A site that really launches clean is seeded like any other, with the reason.

use crate::pylex::{self, Kind, Tok};
use std::path::Path;

/// The text of a string token (the lexer keeps no text for one) without its prefix and quotes.
fn literal(lines: &[Vec<char>], tok: &Tok) -> Option<String> {
    let Kind::Str { bytes: false, .. } = tok.kind else {
        return None;
    };
    let ((l0, c0), (l1, c1)) = (tok.start, tok.end);
    let mut raw = String::new();
    for row in l0..=l1 {
        let line = lines.get(row - 1)?;
        let from = if row == l0 { c0 } else { 0 };
        let to = if row == l1 { c1 } else { line.len() };
        raw.extend(line.get(from..to.min(line.len()))?);
        if row != l1 {
            raw.push('\n');
        }
    }
    let body = raw.trim_start_matches(|c: char| c.is_ascii_alphabetic());
    ["\"\"\"", "'''", "\"", "'"].iter().find_map(|q| {
        body.strip_prefix(q)
            .and_then(|b| b.strip_suffix(q))
            .map(str::to_owned)
    })
}

/// `open` as a command word.
fn is_open_word(s: &str) -> bool {
    s == "open" || s == "/usr/bin/open"
}

/// The callees whose first string argument is a command.
const SPAWNERS: [&str; 12] = [
    "run",
    "call",
    "Popen",
    "check_call",
    "check_output",
    "system",
    "popen",
    "getoutput",
    "getstatusoutput",
    "execv",
    "execvp",
    "spawn",
];

/// Whether token `i` is an argument of a process call: its nearest enclosing bracket is that
/// call's parenthesis (or the argv list inside it).
fn in_spawn_call(toks: &[&Tok], i: usize) -> bool {
    let mut at = i;
    for _ in 0..2 {
        let mut depth = 0usize;
        let Some(open) = (0..at).rev().find(|&j| match toks[j].kind {
            Kind::Close => {
                depth += 1;
                false
            }
            Kind::Open if depth > 0 => {
                depth -= 1;
                false
            }
            Kind::Open => true,
            _ => false,
        }) else {
            return false;
        };
        if toks[open].text == "(" {
            return open > 0
                && toks[open - 1].kind == Kind::Name
                && SPAWNERS.contains(&toks[open - 1].text.as_str());
        }
        at = open;
    }
    false
}

/// A command string that runs `open` at a command position, with something launchable after it.
fn runs_open(s: &str) -> bool {
    s.lines().any(|line| {
        crate::bareopen::scan_line(line).into_iter().any(|at| {
            let rest = &line[at..];
            let rest = rest.strip_prefix("/usr/bin/").unwrap_or(rest);
            let rest = rest.strip_prefix("open").unwrap_or(rest);
            rest.trim_start()
                .starts_with(['-', '/', '"', '\'', '$', '~', '{', '%'])
        })
    })
}

/// The 1-based lines of every `open` launch in a Python source; `None` where the file does not
/// tokenize.
#[must_use]
pub fn scan_text(text: &str) -> Option<Vec<usize>> {
    let lexed = pylex::lex(text)?;
    let toks: Vec<&Tok> = lexed
        .toks
        .iter()
        .filter(|t| t.kind != Kind::Newline)
        .collect();
    let rows: Vec<Vec<char>> = text.split('\n').map(|l| l.chars().collect()).collect();
    let mut out = Vec::new();
    for (i, tok) in toks.iter().enumerate() {
        let Some(s) = literal(&rows, tok) else {
            continue;
        };
        let argv = is_open_word(&s)
            && i > 0
            && toks[i - 1].kind == Kind::Open
            && toks[i - 1].text == "["
            && !(i > 1 && (toks[i - 2].text == "==" || toks[i - 2].text == "!="))
            && toks.get(i + 1).is_some_and(|n| n.text == ",");
        if argv || (runs_open(&s) && in_spawn_call(&toks, i)) {
            out.push(tok.start.0);
        }
    }
    out.dedup();
    Some(out)
}

/// `(path, text)` of every in-scope UTF-8 `.py` file among `files`.
#[must_use]
pub fn sources(
    root: &Path,
    files: &[String],
    exclude: Option<&crate::pathfilter::PathFilter>,
    staged: bool,
) -> Vec<(String, String)> {
    files
        .iter()
        .filter(|rel| {
            Path::new(rel.as_str())
                .extension()
                .is_some_and(|e| e.eq_ignore_ascii_case("py"))
                && !exclude.is_some_and(|x| x.is_match(rel))
        })
        .filter_map(|rel| {
            let blob = crate::gitutil::content_bytes(root, rel, staged)?;
            Some((rel.clone(), String::from_utf8(blob).ok()?))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lines(src: &str) -> Vec<usize> {
        scan_text(src).expect("tokenizes")
    }

    #[test]
    fn each_launch_shape_is_found() {
        for bad in [
            "subprocess.run([\"open\", \"-a\", app])\n",
            "subprocess.run(['/usr/bin/open', '-g', str(app)], check=True)\n",
            "subprocess.run(\n    [\n        \"open\",\n        \"-a\",\n    ]\n)\n",
            "subprocess.run(\"open -a Foo\", shell=True)\n",
            "os.system(f\"open -a {app}\")\n",
            "subprocess.check_output('cd x && open -g \"Y\"', shell=True)\n",
            "subprocess.Popen([\"sh\", \"-c\", \"open -g Y\"])\n",
        ] {
            assert_eq!(lines(bad).len(), 1, "{bad}");
        }
        assert_eq!(lines("x = 1\nsubprocess.run([\n  \"open\",\n])\n"), vec![3]);
    }

    #[test]
    fn prose_comments_and_other_commands_pass() {
        for ok in [
            "print(\"open the trace in Instruments\")\n",
            "# subprocess.run([\"open\", x])\n",
            "\"\"\"Runs open -a on the app.\"\"\"\n",
            "subprocess.run([\"xdg-open\", url])\n",
            "subprocess.run([\"env\", \"-i\", \"/usr/bin/env\"])\n",
            "subprocess.run([GOH, \"launch-app\", app])\n",
            "x = [\"reopen\", \"open_app\"]\n",
            "f.open(\"x\")\n",
            "w = (\"open\", \"close\")\n",
            "assert ledger.uncertain() == [\"open\"]\n",
            "assert cmd[:3] == [\"/usr/bin/open\", \"-g\", \"-a\"]\n",
            "subprocess.Popen([\"open\"])\n",
            "print(\"run open -a Foo to launch\")\n",
            "raise OSError(f\"open {name} failed\")\n",
            "assert 'open \"$APP\"' in out\n",
            "p.write_text('open \"dist/X.app\"\\n')\n",
            "x = \"open -a Foo\"\n",
        ] {
            assert!(lines(ok).is_empty(), "{ok}");
        }
    }

    #[test]
    fn a_file_that_does_not_tokenize_is_not_read() {
        assert!(scan_text("x = (\n").is_none());
    }
}
