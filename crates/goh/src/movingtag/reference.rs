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
//!   * a tag that IS a commit id is the pin nothing can move, beside the digest: `:f93290b3ab1a`
//!     names one build, and nobody can re-point it (THE COMMIT PIN, 2026-10-21).
//!   * every other digit-bearing tag must name a build the same way -- a date, a full `x.y.z`
//!     version (any suffix: `1.99.1-slim-bookworm`) or a commit id. One whose version is coarser
//!     (`rust:1`, `node:22`, `alpine:3`, `redis:7.4`, `postgres:18-alpine`, `pg16`) is a release
//!     LINE: its maintainer re-points it at every release of the line, so it is a moving tag
//!     (OWNER RULING 2026-11-15, asked whether version-shaped tags like `rust:1` count: "Yes, flag
//!     them"; GOH.MOVINGTAGCOARSE). Judged from `crate::grace::OVER_FROM` (2026-11-23): before it
//!     [`verdict_at`] reads such a tag as it did at the last release, a released name.
//!
//! THE DISTRO VERSION (`docs/decisions/D-0007-a-version-coarser-than-x-y-z-is-a-release-line.md`).
//! `ubuntu:24.04` and `debian:13` / `debian:13.1` are release lines like any other, with no
//! exception: Ubuntu's `24.04` is re-pointed across every 24.04.N point release and every rebuild
//! in between, and Debian's `13.1` until 13.2 ships, so the text names a line, not a build. Each
//! distro publishes the tag that does name one build, a dated one (`ubuntu:noble-20250925`,
//! `debian:trixie-20250908`), and a digest pins any of them. An exception would need this file to
//! know which repositories are distributions and which versioning scheme each uses, a list nobody
//! maintains, and would pass `postgres:16.4` and `debian:13.1` while failing `redis:7.4` on the
//! same two-part shape. The rule is the shape: fewer than three version components, no date and no
//! commit id is a line. What the text still cannot see: a full `x.y.z` tag its maintainer rebuilds
//! in place (`alpine:3.20.3` after a CVE fix) passes; only a digest is immune to that.

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
    /// A commit id: the other immovable pin. Nothing can re-point a build's own commit.
    Commit,
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

/// Is this tag a commit id -- the pin nothing can move, beside the digest?
///
/// A tag that IS a commit id names one build, and nobody can re-point it: the maintainer would
/// have to rebuild the image under a different commit to move the tag. The house names the two
/// immovable pins apart (the digest and the commit), because a consumer's own gate has to be
/// ABLE to say "this is pinned to a commit" -- Adv360-Pro-ZMK's base-pin row (P1.3) is blocked on
/// exactly that, and a gate that cannot express the pin cannot accept one.
#[must_use]
pub fn is_commit_id(tag: &str) -> bool {
    is_commit(tag)
}

/// Whether `tag` names one build: a date, a full `x.y.z` version or a commit id among its parts.
fn names_a_build(tag: &str, parts: &[String]) -> bool {
    has_date(tag) || has_full_version(tag) || parts.iter().any(|p| is_commit(p))
}

fn parts_of(tag: &str) -> Vec<String> {
    tag.split(['-', '_', '.'])
        .map(str::to_ascii_lowercase)
        .collect()
}

/// Whether `tag` is a CHANNEL: no digit, or a channel word with no build named beside it.
///
/// All the scanner judged before GOH.MOVINGTAGCOARSE, and all it judges within `crate::grace`.
#[must_use]
pub fn is_channel(tag: &str) -> bool {
    if !tag.bytes().any(|b| b.is_ascii_digit()) {
        return true;
    }
    let parts = parts_of(tag);
    parts.iter().any(|p| CHANNELS.contains(&p.as_str())) && !names_a_build(tag, &parts)
}

/// Whether `tag` is a pointer someone moves rather than the name of one build: a channel, or a
/// release line (a version coarser than `x.y.z` with no date or commit id beside it).
#[must_use]
pub fn is_moving_tag(tag: &str) -> bool {
    is_moving_tag_at(tag, true)
}

/// [`is_moving_tag`], with release lines judged (`lines`) or passed as the grace has them.
#[must_use]
pub fn is_moving_tag_at(tag: &str, lines: bool) -> bool {
    is_channel(tag) || (lines && !names_a_build(tag, &parts_of(tag)))
}

/// Judge one reference, already substituted ([`substitute`]), release lines included.
#[must_use]
pub fn verdict(raw: &str) -> Verdict {
    verdict_at(raw, true)
}

/// [`verdict`], with release lines judged (`lines`, from `crate::grace::OVER_FROM`) or passed as a
/// released name (before it: the verdict of the last release).
#[must_use]
pub fn verdict_at(raw: &str, lines: bool) -> Verdict {
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
        Some(t) if is_moving_tag_at(t, lines) => Verdict::Moving(t.to_owned()),
        Some(t) if is_commit_id(t) => Verdict::Commit,
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
