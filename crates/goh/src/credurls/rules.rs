//! The URL split and the value rules: `urlsplit` as Python reads it, the
//! credential shapes, and the helper/header judgement (`_credential_config`).

use std::fmt::Write as _;

use regex::Regex;
use sha2::{Digest, Sha256};

/// A stable, non-reversible handle for a credential.
#[must_use]
pub fn fingerprint(secret: &str) -> String {
    let digest = Sha256::digest(secret.as_bytes());
    digest
        .iter()
        .take(6)
        .fold(String::from("sha256:"), |mut out, b| {
            let _ = write!(out, "{b:02x}");
            out
        })
}

/// The parts of `urllib.parse.urlsplit(url)` this gate reads.
pub(super) struct Split {
    pub(super) scheme: String,
    pub(super) netloc: String,
}

pub(super) fn urlsplit(raw: &str) -> Split {
    let url: String = raw
        .trim_start_matches(|c: char| c <= ' ')
        .chars()
        .filter(|c| !matches!(c, '\t' | '\r' | '\n'))
        .collect();
    let mut rest = url.as_str();
    let mut scheme = String::new();
    if let Some(i) = rest.find(':') {
        let head = &rest[..i];
        if i > 0
            && head.starts_with(|c: char| c.is_ascii_alphabetic())
            && head
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || "+-.".contains(c))
        {
            scheme = head.to_ascii_lowercase();
            rest = &rest[i + 1..];
        }
    }
    let netloc = rest.strip_prefix("//").map_or_else(String::new, |after| {
        let end = after.find(['/', '?', '#']).unwrap_or(after.len());
        after[..end].to_owned()
    });
    Split { scheme, netloc }
}

/// The rules, compiled once.
pub struct Rules {
    token_prefix: Regex,
    opaque: Regex,
    secrets: Vec<(&'static str, Regex)>,
    helper_assign: Regex,
    credential_header: Regex,
    auth_scheme: Regex,
    label_host: Regex,
}

impl Rules {
    /// # Errors
    /// A built-in pattern that does not compile.
    pub fn new() -> Result<Self, String> {
        let rx = |p: &str| Regex::new(p).map_err(|e| format!("credential pattern {p:?}: {e}"));
        Ok(Self {
            token_prefix: rx(
                r"^(gh[pousr]_|github_pat_|sk-|sk-ant-|xox[bpas]-|AKIA|glpat-|npm_|dop_v1_|glsa-|ya29\.)",
            )?,
            opaque: rx(r"^[A-Za-z0-9._~+/=-]{20,}$")?,
            secrets: crate::secrets::compile_all()?.0,
            helper_assign: rx(r#"(?i)\b(?:password|token)=([^\s;'"`]+)"#)?,
            credential_header: rx(
                r"(?i)^\s*(?:authorization|proxy-authorization|[\w-]*token|[\w-]*api-?key|cookie)\s*:\s*(.*)$",
            )?,
            auth_scheme: rx(r"(?i)^(?:basic|bearer|token|digest|negotiate)\s+")?,
            label_host: rx(r"(?i)^([a-z]+://)?(?:[^@/]*@)?([^/]*).*$")?,
        })
    }

    /// `(kind, host, userinfo, why)` for a URL carrying a credential, else None.
    pub(super) fn verdict(
        &self,
        url: &str,
    ) -> Option<(&'static str, String, String, &'static str)> {
        let parts = urlsplit(url);
        let (userinfo, hostinfo) = parts.netloc.rsplit_once('@')?;
        let host = match hostinfo.split_once('[') {
            Some((_, bracketed)) => bracketed.split_once(']').map_or(bracketed, |(h, _)| h),
            None => hostinfo.split_once(':').map_or(hostinfo, |(h, _)| h),
        }
        .to_lowercase();
        if userinfo.contains(':') {
            return Some((
                "userinfo-with-password",
                host,
                userinfo.to_owned(),
                "a password in a remote URL",
            ));
        }
        let credential = !userinfo.is_empty()
            && (self.token_prefix.is_match(userinfo) || self.opaque.is_match(userinfo));
        credential.then(|| {
            (
                "userinfo-credential",
                host,
                userinfo.to_owned(),
                "a credential-shaped value where a username belongs",
            )
        })
    }

    /// `(kind, secret, why)` for a helper or header value, else None.
    pub(super) fn judge(&self, key: &str, value: &str) -> Option<(String, String, String)> {
        let slot = key.rsplit('.').next().unwrap_or(key);
        for (kind, pattern) in &self.secrets {
            if let Some(m) = pattern.find(value) {
                return Some((
                    (*kind).to_owned(),
                    m.as_str().to_owned(),
                    format!("a {kind} in {slot}"),
                ));
            }
        }
        let literal = |v: &str| !v.is_empty() && !v.starts_with('$');
        if key.ends_with(".helper") {
            return self.helper_assign.captures_iter(value).find_map(|c| {
                let v = c.get(1)?.as_str();
                literal(v).then(|| {
                    (
                        "helper-literal".to_owned(),
                        v.to_owned(),
                        "a literal password in a credential helper".to_owned(),
                    )
                })
            });
        }
        let header = self.credential_header.captures(value)?;
        let credential = self
            .auth_scheme
            .replace(header.get(1)?.as_str().trim(), "")
            .into_owned();
        literal(&credential).then(|| {
            (
                "header-credential".to_owned(),
                credential,
                "a literal credential in an HTTP header".to_owned(),
            )
        })
    }

    /// The key with any URL subsection reduced to `scheme://host` (`label`).
    pub(super) fn label(&self, key: &str) -> String {
        let (section, tail) = key.split_once('.').unwrap_or((key, ""));
        let Some((sub, var)) = tail.rsplit_once('.').filter(|(s, _)| !s.is_empty()) else {
            return key.to_owned();
        };
        let host = self.label_host.replace(sub, "$1$2");
        format!("{section}.{host}.{var}")
    }
}
