//! Which commands stop reading their stdin before EOF.
//!
//! A consumer that exits early closes the pipe while its producer may still be writing; the
//! producer's next write dies of SIGPIPE (exit 141), and pipefail makes that the pipeline's
//! status. Whether the producer had finished writing first is load: so the verdict is a race.
//! The forms here are the ones measured to exit early (2026-10-08): `grep -q`/`--quiet`/`--silent`
//! and `-l` (stop at the first match), `grep -m N`/`--max-count`, `head` (unless `-n -N`, which
//! reads to EOF), an awk program that calls `exit` outside `END`, and a sed script with `q`/`Q`.

/// The consumer kind `cmd args...` is, if it exits before EOF.
#[must_use]
pub fn classify(cmd: &str, args: &[String]) -> Option<&'static str> {
    match cmd {
        "grep" | "egrep" | "fgrep" | "ggrep" | "zgrep" => grep(args),
        "head" | "ghead" => head(args).then_some("head"),
        "awk" | "gawk" | "mawk" | "nawk" => awk_program(args)
            .is_some_and(|p| awk_exits(&p))
            .then_some("awk exit"),
        "sed" | "gsed" => sed_scripts(args)
            .iter()
            .any(|s| sed_quits(s))
            .then_some("sed q"),
        _ => None,
    }
}

fn grep(args: &[String]) -> Option<&'static str> {
    let mut words = args.iter();
    while let Some(w) = words.next() {
        if w == "--" {
            break;
        }
        if let Some(long) = w.strip_prefix("--") {
            let name = long.split('=').next().unwrap_or(long);
            match name {
                "quiet" | "silent" | "files-with-matches" => return Some("grep -q"),
                "max-count" => return Some("grep -m"),
                "regexp" | "file" | "context" | "after-context" | "before-context"
                    if !long.contains('=') =>
                {
                    words.next();
                }
                _ => {}
            }
            continue;
        }
        let Some(cluster) = w.strip_prefix('-').filter(|c| !c.is_empty()) else {
            continue;
        };
        for (at, c) in cluster.char_indices() {
            match c {
                'q' | 'l' => return Some("grep -q"),
                'm' => return Some("grep -m"),
                'e' | 'f' | 'A' | 'B' | 'C' | 'd' | 'D' => {
                    if at + 1 == cluster.len() {
                        words.next();
                    }
                    break;
                }
                _ => {}
            }
        }
    }
    None
}

/// `head` stops early unless told to print all but the last N (`-n -N`, `-c -N`), which reads all.
fn head(args: &[String]) -> bool {
    let mut words = args.iter();
    while let Some(w) = words.next() {
        let value = match w.as_str() {
            "-n" | "-c" | "--lines" | "--bytes" => words.next().map(String::as_str),
            _ => w
                .strip_prefix("--lines=")
                .or_else(|| w.strip_prefix("--bytes="))
                .or_else(|| w.strip_prefix("-n"))
                .or_else(|| w.strip_prefix("-c")),
        };
        if value.is_some_and(|v| v.starts_with('-')) {
            return false;
        }
    }
    true
}

/// The program text an awk invocation runs, when it is on the command line.
fn awk_program(args: &[String]) -> Option<String> {
    let mut words = args.iter();
    while let Some(w) = words.next() {
        match w.as_str() {
            "-f" | "--file" => return None,
            "-F" | "-v" | "--assign" | "--field-separator" => {
                words.next();
            }
            "--" => return words.next().cloned(),
            _ if w.starts_with('-') && w.len() > 1 => {}
            _ => return Some(w.clone()),
        }
    }
    None
}

/// The program with every `END { ... }` block removed (an exit there runs after EOF).
fn without_end_blocks(prog: &str) -> String {
    let c: Vec<char> = prog.chars().collect();
    let mut out = String::new();
    let mut i = 0usize;
    while i < c.len() {
        let boundary = i == 0 || !(c[i - 1].is_alphanumeric() || c[i - 1] == '_');
        if boundary && c[i..].starts_with(&['E', 'N', 'D']) {
            let mut j = i + 3;
            while c.get(j).is_some_and(|x| x.is_whitespace()) {
                j += 1;
            }
            if c.get(j) == Some(&'{') {
                let mut depth = 0usize;
                while j < c.len() {
                    match c[j] {
                        '{' => depth += 1,
                        '}' => {
                            depth -= 1;
                            if depth == 0 {
                                break;
                            }
                        }
                        _ => {}
                    }
                    j += 1;
                }
                i = j + 1;
                continue;
            }
        }
        out.push(c[i]);
        i += 1;
    }
    out
}

fn awk_exits(prog: &str) -> bool {
    let body = without_end_blocks(prog);
    let c: Vec<char> = body.chars().collect();
    let word = |x: Option<&char>| x.is_some_and(|x| x.is_alphanumeric() || *x == '_');
    (0..c.len()).any(|i| {
        c[i..].starts_with(&['e', 'x', 'i', 't'])
            && !word(i.checked_sub(1).and_then(|p| c.get(p)))
            && !word(c.get(i + 4))
    })
}

/// The sed scripts on the command line (`-e S`, `--expression=S`, else the first operand).
fn sed_scripts(args: &[String]) -> Vec<String> {
    let mut scripts = Vec::new();
    let mut operand: Option<String> = None;
    let mut words = args.iter();
    while let Some(w) = words.next() {
        if let Some(s) = w.strip_prefix("--expression=") {
            scripts.push(s.to_owned());
        } else if w == "--expression" {
            scripts.extend(words.next().cloned());
        } else if w == "-f" || w.starts_with("--file") {
            return Vec::new();
        } else if let Some(cluster) = w.strip_prefix('-').filter(|c| !c.is_empty()) {
            if let Some(at) = cluster.find(['e', 'f']) {
                if cluster[at..].starts_with('f') {
                    return Vec::new();
                }
                let rest = &cluster[at + 1..];
                if rest.is_empty() {
                    scripts.extend(words.next().cloned());
                } else {
                    scripts.push(rest.to_owned());
                }
            }
        } else if operand.is_none() {
            operand = Some(w.clone());
        }
    }
    if scripts.is_empty() {
        scripts.extend(operand);
    }
    scripts
}

/// Past one delimited part (`/re/`, `|x|`) starting AFTER its opening delimiter.
const fn past_delimited(c: &[char], mut i: usize, delim: char) -> usize {
    while i < c.len() && c[i] != delim {
        i += if c[i] == '\\' { 2 } else { 1 };
    }
    i + 1
}

fn past_address(c: &[char], mut i: usize) -> usize {
    match c.get(i) {
        Some(d) if d.is_ascii_digit() => {
            while c.get(i).is_some_and(|x| x.is_ascii_digit() || *x == '~') {
                i += 1;
            }
        }
        Some('$') => i += 1,
        Some('/') => {
            i = past_delimited(c, i + 1, '/');
            while matches!(c.get(i), Some('I' | 'M')) {
                i += 1;
            }
        }
        Some('\\') => {
            if let Some(&d) = c.get(i + 1) {
                i = past_delimited(c, i + 2, d);
            }
        }
        _ => {}
    }
    i
}

/// Does a sed script run `q` or `Q`? A light parse: addresses, `s`/`y` bodies and text
/// arguments are skipped, so a `q` inside a regex or replacement is not a command.
#[must_use]
pub fn sed_quits(script: &str) -> bool {
    let c: Vec<char> = script.chars().collect();
    let mut i = 0usize;
    let to = |c: &[char], mut i: usize, stops: &[char]| {
        while i < c.len() && !stops.contains(&c[i]) {
            i += 1;
        }
        i
    };
    while i < c.len() {
        while c
            .get(i)
            .is_some_and(|x| x.is_whitespace() || ";{}".contains(*x))
        {
            i += 1;
        }
        if i >= c.len() {
            break;
        }
        i = past_address(&c, i);
        if c.get(i) == Some(&',') {
            i = past_address(&c, i + 1);
        }
        while c.get(i).is_some_and(|x| x.is_whitespace() || *x == '!') {
            i += 1;
        }
        let Some(&cmd) = c.get(i) else { break };
        i += 1;
        match cmd {
            'q' | 'Q' => return true,
            's' | 'y' => {
                if let Some(&d) = c.get(i) {
                    i = past_delimited(&c, i + 1, d);
                    i = past_delimited(&c, i, d);
                }
                i = to(&c, i, &[';', '\n', '}']);
            }
            'a' | 'i' | 'c' | '#' => i = to(&c, i, &['\n']),
            ':' | 'b' | 't' | 'T' | 'r' | 'R' | 'w' | 'W' | 'e' | 'v' => {
                i = to(&c, i, &[';', '\n']);
            }
            _ => {}
        }
    }
    false
}
