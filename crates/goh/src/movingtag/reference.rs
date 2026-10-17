//! What one container image reference pins, read from its text alone.
//!
//! `name[:tag][@digest]`, with an optional `docker://` scheme (a workflow's `uses:`). The tag is
//! the part after the last `:` of the LAST path component, so a registry port
//! (`localhost:5000/app`) is not a tag. A digest pins the image whatever the tag says; with no
//! digest the tag must name ONE build:
//!
//!   * no tag at all is `latest`, implicitly;
//!   * a tag with no digit in it is a channel, never a release -- `latest`, `stable`, `edge`,
//!     `alpine`, `bookworm`, `lts`: each is a pointer its maintainer moves, and a release name
//!     carries a number (a version, a date, a build id);
//!   * a tag WITH digits that spells a channel word as one of its `-`/`_`/`.` parts (`3.5-branch`,
//!     `v2-latest`, `1.2-dev`) is that channel of a release line, unless another part names one
//!     build: a date (`nightly-2025-01-01`, `-20250930025731`), a full `x.y.z` version
//!     (`1.2.3-dev`) or a commit id (`dev-a1b2c3d`).
//!
//! NOT judged: how coarse a version-shaped tag is. `alpine:3`, `postgres:18-alpine` and
//! `ubuntu:24.04` all float across rebuilds, but telling a release line from a release needs the
//! project's own versioning scheme, and the house rule asks for a NAMED released version, which
//! these are; the only pin nothing can move is the digest.

use std::collections::BTreeMap;

/// Channel words: each names a pointer its maintainer moves, not a build.
///
/// Compared against a whole `-`/`_`/`.` part of the tag, ignoring case. A tag with no digit is a
/// channel without consulting this list; the list is what makes a digit-bearing tag like
/// `3.5-branch` one.
pub const CHANNELS: [&str; 21] = [
    "latest", "stable", "main", "master", "edge", "nightly", "dev", "develop", "devel", "rolling",
    "canary", "unstable", "testing", "trunk", "head", "snapshot", "branch", "next", "current",
    "insiders", "preview",
];

/// What a reference pins.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// Not an image a registry serves: empty, or `scratch` (Docker's empty base).
    NotAReference,
    /// Carried by a variable or template the file does not resolve: nothing to judge.
    Unreadable,
    /// `@<algorithm>:<hex>`: immovable.
    Digest,
    /// A tag that names one build.
    Released,
    /// No tag: whatever `latest` points at.
    NoTag,
    /// A moving tag, as written.
    Moving(String),
}

fn is_digest(s: &str) -> bool {
    s.split_once(':').is_some_and(|(alg, hex)| {
        !alg.is_empty()
            && alg
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b"+._-".contains(&b))
            && hex.len() >= 32
            && hex.bytes().all(|b| b.is_ascii_hexdigit())
    })
}

fn has_date(tag: &str) -> bool {
    let b = tag.as_bytes();
    let mut run = 0usize;
    for &c in b {
        run = if c.is_ascii_digit() { run + 1 } else { 0 };
        if run >= 8 {
            return true;
        }
    }
    // YYYY-MM-DD / YYYY.MM.DD / YYYY_MM_DD
    b.windows(10).any(|w| {
        w[..4].iter().all(u8::is_ascii_digit)
            && b"-._".contains(&w[4])
            && w[5..7].iter().all(u8::is_ascii_digit)
            && w[7] == w[4]
            && w[8..].iter().all(u8::is_ascii_digit)
    })
}

fn is_number(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit())
}

/// A full `x.y.z` version somewhere in the tag (an optional `v`, any trailing suffix).
fn has_full_version(tag: &str) -> bool {
    tag.split(['-', '_']).any(|part| {
        let p = part.strip_prefix(['v', 'V']).unwrap_or(part);
        let nums: Vec<&str> = p.split('.').collect();
        nums.len() >= 3 && nums.iter().take(3).all(|n| is_number(n))
    })
}

/// A commit id: 7 to 40 hex characters holding both a letter and a digit.
fn is_commit(part: &str) -> bool {
    (7..=40).contains(&part.len())
        && part.bytes().all(|b| b.is_ascii_hexdigit())
        && part.bytes().any(|b| b.is_ascii_digit())
        && part.bytes().any(|b| b.is_ascii_alphabetic())
}

/// Whether `tag` is a pointer someone moves rather than the name of one build.
#[must_use]
pub fn is_moving_tag(tag: &str) -> bool {
    if !tag.bytes().any(|b| b.is_ascii_digit()) {
        return true;
    }
    let parts: Vec<String> = tag
        .split(['-', '_', '.'])
        .map(str::to_ascii_lowercase)
        .collect();
    if !parts.iter().any(|p| CHANNELS.contains(&p.as_str())) {
        return false;
    }
    !(has_date(tag) || has_full_version(tag) || parts.iter().any(|p| is_commit(p)))
}

/// Judge one reference, already substituted ([`substitute`]).
#[must_use]
pub fn verdict(raw: &str) -> Verdict {
    let r = raw.trim().trim_matches(['"', '\'']).trim();
    let r = r.strip_prefix("docker://").unwrap_or(r);
    if r.is_empty() {
        return Verdict::NotAReference;
    }
    if r.contains("{{") {
        return Verdict::Unreadable;
    }
    let name = match r.split_once('@') {
        Some((_, digest)) if is_digest(digest) => return Verdict::Digest,
        Some((name, _)) => name,
        None => r,
    };
    let (prefix, last) = name.rsplit_once('/').unwrap_or(("", name));
    let (repo, tag) = match last.split_once(':') {
        Some((repo, tag)) => (repo, Some(tag)),
        None => (last, None),
    };
    if last.contains('$') && tag.is_none_or(|t| t.contains('$')) {
        return Verdict::Unreadable;
    }
    match tag {
        None if prefix.is_empty() && repo.eq_ignore_ascii_case("scratch") => Verdict::NotAReference,
        None => Verdict::NoTag,
        Some(t) if is_moving_tag(t) => Verdict::Moving(t.to_owned()),
        Some(_) => Verdict::Released,
    }
}

/// `text` with `$NAME`, `${NAME}`, `${NAME:-default}` and `${NAME-default}` resolved.
///
/// From `vars`, a default standing in for a name `vars` lacks. A name with neither is left as
/// written, so [`verdict`] sees the `$` and calls the reference unreadable. `$$` is a literal `$`
/// (compose).
#[must_use]
pub fn substitute(text: &str, vars: &BTreeMap<String, String>) -> String {
    let b = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] != b'$' {
            let ch = text[i..].chars().next().unwrap_or(' ');
            out.push(ch);
            i += ch.len_utf8();
            continue;
        }
        let rest = &text[i + 1..];
        if let Some(r) = rest.strip_prefix('$') {
            // `$$` escapes a dollar; keep both so nothing downstream reads a variable there.
            out.push_str("$$");
            i = text.len() - r.len();
        } else if let Some(body) = rest.strip_prefix('{').and_then(|r| r.split_once('}')) {
            let (inner, _) = body;
            let name_len = inner
                .bytes()
                .take_while(|c| c.is_ascii_alphanumeric() || *c == b'_')
                .count();
            let (name, op) = inner.split_at(name_len);
            let default = op.strip_prefix(":-").or_else(|| op.strip_prefix('-'));
            match (vars.get(name), default) {
                (Some(v), _) if !v.is_empty() || default.is_none() => out.push_str(v),
                (_, Some(d)) => out.push_str(d),
                _ => {
                    out.push_str("${");
                    out.push_str(inner);
                    out.push('}');
                }
            }
            i += 2 + inner.len() + 1;
        } else {
            let name_len = rest
                .bytes()
                .take_while(|c| c.is_ascii_alphanumeric() || *c == b'_')
                .count();
            let name = &rest[..name_len];
            match vars.get(name) {
                Some(v) if name_len > 0 => out.push_str(v),
                _ => {
                    out.push('$');
                    out.push_str(name);
                }
            }
            i += 1 + name_len;
        }
    }
    out
}
