//! Version-source TEXT -> the release numbers it declares.
//!
//! Port of the retired `checks/_version_sources.py` (Phase N1): one registry, keyed by the `kind`
//! a repo names in `GOH_TAG_VERSION_SOURCES`. Selection is by VALUE SHAPE,
//! never by position -- these files carry more than one plausible-looking
//! declaration (a marketing version AND a build number).

use regex::Regex;

/// `(table, version)` declarations.
pub type Declared = Vec<(String, String)>;

/// The compiled strategies.
pub struct Strategies {
    cargo_version: Regex,
    table: Regex,
    swift: Regex,
    semver: Regex,
    xcconfig: Regex,
    plist_key: Regex,
    plist_string: Regex,
    xcodegen: Regex,
    gradle: Regex,
}

/// The kinds: the frozen reference's six in its order, then the ones added since (native only).
pub const KINDS: [&str; 8] = [
    "file",
    "cargo",
    "swift",
    "xcconfig",
    "plist",
    "pyproject",
    "xcodegen",
    "gradle",
];
/// The default `GOH_TAG_VERSION_SOURCES`.
pub const DEFAULT_SOURCES: [&str; 2] = ["file:VERSION", "cargo:Cargo.toml"];

impl Strategies {
    /// # Errors
    /// A built-in pattern that does not compile.
    pub fn new() -> Result<Self, String> {
        let rx = |p: &str| Regex::new(p).map_err(|e| format!("version-source pattern {p:?}: {e}"));
        Ok(Self {
            cargo_version: rx(r#"^\s*version\s*=\s*["']([^"']+)["']"#)?,
            table: rx(r"^\s*\[\[?\s*([A-Za-z0-9_.\-]+)\s*\]?\]")?,
            swift: rx(
                r#"^\s*(?:public\s+|private\s+|internal\s+|fileprivate\s+|open\s+)*(?:static\s+)?(?:let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*["']([^"']+)["']"#,
            )?,
            semver: rx(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?$")?,
            xcconfig: rx(
                r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[[^\]]*\])?\s*=\s*(.*?)\s*(?://.*)?$",
            )?,
            plist_key: rx(r"^<key>\s*([^<]*)</\s*key\s*>")?,
            plist_string: rx(r"^<string>\s*([^<]*)</\s*string\s*>")?,
            // XcodeGen's `project.yml` sets the build setting as a YAML key. Only this key: the
            // same file pins package and tool versions that are not the release's.
            xcodegen: rx(r#"^\s*MARKETING_VERSION\s*:\s*["']?([^"'\s#]+)["']?\s*(?:#.*)?$"#)?,
            // Android's `build.gradle(.kts)`: `versionName = "x"` (Kotlin) or `versionName "x"`
            // (Groovy). Only this name: dependency coordinates carry versions too.
            gradle: rx(r#"^\s*versionName\s*(?:=\s*)?["']([^"']+)["']"#)?,
        })
    }

    /// The declarations `kind` reads from `text`, or `None` for an unknown kind.
    #[must_use]
    pub fn extract(&self, kind: &str, text: &str) -> Option<Declared> {
        let lines = crate::mdtext::splitlines(text);
        let cap = |r: &Regex, s: &str, g: usize| {
            r.captures(s)
                .and_then(|c| c.get(g))
                .map(|m| m.as_str().to_owned())
        };
        let mut out = Vec::new();
        match kind {
            "file" => {
                if let Some(line) = lines
                    .iter()
                    .map(|l| l.trim())
                    .find(|l| !l.is_empty() && !l.starts_with('#'))
                {
                    out.push(("(file)".to_owned(), line.to_owned()));
                }
            }
            "cargo" | "pyproject" => {
                let mut table: Option<String> = if kind == "cargo" {
                    Some(String::new())
                } else {
                    None
                };
                for raw in lines {
                    if let Some(t) = cap(&self.table, raw, 1) {
                        table = Some(t);
                        continue;
                    }
                    let Some(v) = cap(&self.cargo_version, raw, 1) else {
                        continue;
                    };
                    let t = table.as_deref().unwrap_or("");
                    if kind == "cargo" && matches!(t, "workspace.package" | "package") {
                        out.push((t.to_owned(), v));
                    } else if kind == "pyproject"
                        && matches!(t, "project" | "tool.poetry")
                        && self.semver.is_match(&v)
                    {
                        out.push((format!("({t})"), v));
                    }
                }
            }
            "swift" | "xcconfig" => {
                let (rx, tag) = if kind == "swift" {
                    (&self.swift, "swift")
                } else {
                    (&self.xcconfig, "xcconfig")
                };
                for raw in lines {
                    if let (Some(name), Some(v)) = (cap(rx, raw, 1), cap(rx, raw, 2)) {
                        if self.semver.is_match(&v) {
                            out.push((format!("({tag}:{name})"), v));
                        }
                    }
                }
            }
            "plist" => {
                let mut key: Option<String> = None;
                for raw in lines {
                    let line = raw.trim();
                    if let Some(k) = cap(&self.plist_key, line, 1) {
                        key = Some(k);
                        continue;
                    }
                    let Some(v) = cap(&self.plist_string, line, 1) else {
                        continue;
                    };
                    if let Some(k) = key.as_ref().filter(|k| !k.is_empty()) {
                        if self.semver.is_match(&v) {
                            out.push((format!("(plist:{k})"), v));
                        }
                    }
                    key = None;
                }
            }
            "xcodegen" | "gradle" => {
                let (rx, label) = if kind == "xcodegen" {
                    (&self.xcodegen, "(xcodegen:MARKETING_VERSION)")
                } else {
                    (&self.gradle, "(gradle:versionName)")
                };
                for raw in lines {
                    if let Some(v) = cap(rx, raw, 1).filter(|v| self.semver.is_match(v)) {
                        out.push((label.to_owned(), v));
                    }
                }
            }
            _ => return None,
        }
        Some(out)
    }
}

/// `kind:path` sources, whitespace-separated.
///
/// # Errors
/// An entry with no `:` or an unknown kind -- the reference's message.
pub fn parse_sources(spec: &str) -> Result<Vec<(String, String)>, String> {
    spec.split_whitespace()
        .map(|item| match item.split_once(':') {
            Some((kind, path)) if KINDS.contains(&kind) => Ok((kind.to_owned(), path.to_owned())),
            _ => Err(format!(
                "unknown version source '{item}' (expected kind:path, kind in {})",
                KINDS.join("/")
            )),
        })
        .collect()
}
