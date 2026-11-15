//! `goh bare-open` over Python: a subprocess that runs `open` (GOH.BAREOPEN2).
//!
//! The shell scan reads shell sources; a Python tool that runs `open` through `subprocess` hands the
//! launched app the same caller environment, and was invisible to it. Two shapes, read off the
//! token stream (`crate::pylex`, so a comment or a docstring is never read):
//!
//! * an argv list, judged as argv (GOH.LAUNCHAPPID), never its elements as command lines: past
//!   `nohup`/`sudo`/`time`/`exec`/`command` and an `env` that clears nothing (`["env", "FOO=1",
//!   "open", ...]`), the command word is `open` or `/usr/bin/open`, or a shell whose `-c` string
//!   runs `open`. An `env -i` is the clean launch, by design. A string that is an ARGUMENT of
//!   another program (`["git", "commit", "-m", "open -a X"]`) launches nothing. The `open` needs
//!   a second element, so `== ["open"]` is not one, and a list compared with `==`/`!=` is data;
//! * a command string handed to a process call (`run`, `call`, `Popen`, `system`, ...): `"open -a
//!   Foo"`, `f"/usr/bin/open {app}"`, `"cd x && open -g Y"`. A string is judged by the shell scan
//!   (`crate::bareopen::scan_line`) AND must put an option, a path or a variable after `open`; a
//!   string anywhere else (a message, a docstring, an assert, shell text a test writes to a file)
//!   launches nothing and is prose.
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

/// The index of the bracket that most closely encloses token `i`.
fn enclosing(toks: &[&Tok], i: usize) -> Option<usize> {
    let mut depth = 0usize;
    (0..i).rev().find(|&j| match toks[j].kind {
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
    })
}

/// Whether token `i` is an argument of a process call: its nearest enclosing bracket is that
/// call's parenthesis.
fn in_spawn_call(toks: &[&Tok], i: usize) -> bool {
    enclosing(toks, i).is_some_and(|open| {
        toks[open].text == "("
            && open > 0
            && toks[open - 1].kind == Kind::Name
            && SPAWNERS.contains(&toks[open - 1].text.as_str())
    })
}

/// One element of a list literal: a lone string literal's text (else `None`) and its first token.
type Elem = (Option<String>, usize);

/// The elements of the list literal whose `[` is `toks[at]`. A trailing comma leaves an empty
/// last element, so `["open",]` has two and `["open"]` one.
fn list_elems(rows: &[Vec<char>], toks: &[&Tok], at: usize) -> Vec<Elem> {
    let mut out = Vec::new();
    let (mut depth, mut from) = (0usize, at + 1);
    let close = |from: usize, to: usize, out: &mut Vec<Elem>| {
        let text = (to == from + 1)
            .then(|| literal(rows, toks[from]))
            .flatten();
        out.push((text, from));
    };
    for (j, t) in toks.iter().enumerate().skip(at + 1) {
        match t.kind {
            Kind::Open => depth += 1,
            Kind::Close if depth == 0 => {
                close(from, j, &mut out);
                return out;
            }
            Kind::Close => depth -= 1,
            _ if depth == 0 && t.text == "," => {
                close(from, j, &mut out);
                from = j + 1;
            }
            _ => {}
        }
    }
    out
}

/// The program a word names (`/usr/bin/env` is `env`).
fn base(w: &str) -> &str {
    w.rsplit('/').next().unwrap_or(w)
}

/// The token of the `open` an argv list runs with the caller's environment, if it runs one.
fn argv_launch(el: &[Elem]) -> Option<usize> {
    use crate::bareopen::{env_clears, env_takes_value, is_assignment, is_short_opt};
    let word = |k: usize| el.get(k).and_then(|(s, _)| s.as_deref());
    let mut k = 0;
    while word(k).is_some_and(|w| matches!(base(w), "nohup" | "sudo" | "time" | "exec" | "command"))
    {
        k += 1;
    }
    if word(k).map(base) == Some("env") {
        k += 1;
        loop {
            let w = word(k)?;
            if env_clears(w) {
                return None;
            }
            if env_takes_value(w) {
                k += 2;
            } else if is_short_opt(w) || w.starts_with("--") || is_assignment(w) {
                k += 1;
            } else {
                break;
            }
        }
    }
    let cmd = word(k)?;
    if is_open_word(cmd) {
        return (k + 1 < el.len()).then_some(el[k].1);
    }
    if matches!(base(cmd), "sh" | "bash" | "zsh" | "dash" | "ksh") {
        let c = (k + 1..el.len())
            .find(|&j| word(j).is_some_and(|w| is_short_opt(w) && w.contains('c')))?;
        let (script, at) = el.get(c + 1)?;
        return script.as_deref().is_some_and(runs_open).then_some(*at);
    }
    None
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
        if tok.kind == Kind::Open && tok.text == "[" {
            let compared = i > 0 && (toks[i - 1].text == "==" || toks[i - 1].text == "!=");
            if !compared {
                if let Some(at) = argv_launch(&list_elems(&rows, &toks, i)) {
                    out.push(toks[at].start.0);
                }
            }
            continue;
        }
        let Some(s) = literal(&rows, tok) else {
            continue;
        };
        let in_list = enclosing(&toks, i).is_some_and(|o| toks[o].text == "[");
        if !in_list && runs_open(&s) && in_spawn_call(&toks, i) {
            out.push(tok.start.0);
        }
    }
    out.sort_unstable();
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
    fn an_argv_list_is_judged_as_argv_not_as_command_lines() {
        // Red: `open` behind a wrapper that keeps the caller's environment, or a shell's `-c`.
        for bad in [
            "subprocess.run([\"env\", \"/usr/bin/open\", \"-a\", app])\n",
            "subprocess.run([\"/usr/bin/env\", \"FOO=1\", \"open\", \"-g\", app])\n",
            "subprocess.run([\"nohup\", \"open\", \"-a\", app])\n",
            "cmd = [\"env\", \"-u\", \"X\", \"open\", app]\n",
            "subprocess.run([\"bash\", \"-lc\", f\"open -a {app}\"])\n",
        ] {
            assert_eq!(lines(bad).len(), 1, "{bad}");
        }
        // Green: a cleared environment is the clean launch, by design; a string that is an
        // ARGUMENT of another program (a commit message, an echo) launches nothing.
        for ok in [
            "subprocess.run([\"env\", \"-i\", \"/usr/bin/open\", \"-a\", app])\n",
            "subprocess.run([\"/usr/bin/env\", \"--ignore-environment\", \"open\", app])\n",
            "subprocess.run([\"echo\", \"open -a Foo\"])\n",
            "subprocess.run([\"git\", \"commit\", \"-m\", \"open -g Y\"], check=True)\n",
            "subprocess.run([\"env\", \"-i\", \"sh\", \"-c\", \"open -a Foo\"])\n",
            "subprocess.run([GOH, \"launch-app\", \"-b\", \"com.example.App\"])\n",
        ] {
            assert!(lines(ok).is_empty(), "{ok}");
        }
    }

    #[test]
    fn a_file_that_does_not_tokenize_is_not_read() {
        assert!(scan_text("x = (\n").is_none());
    }
}
