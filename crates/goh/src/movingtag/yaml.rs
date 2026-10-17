//! The images a YAML file names, read from its parse (`saphyr`, with source spans).
//!
//! One rule covers every format that spells an image as `image: <ref>` -- compose services, a
//! workflow's `container.image` and `services.*.image`, Kubernetes containers, CI images -- so
//! every `*.yml`/`*.yaml` is read, wherever it lives (`media_server` keeps its compose files in
//! `compose/*.yml`, a name no filename rule would know). The parse decides what a key is: an
//! `image:` inside a `run: |` block is script text, and an alias resolves to what it names.
//!
//! What counts:
//!   * `image: <scalar>`, unless the same mapping has `build:` -- compose then NAMES the image it
//!     builds, it does not pull it -- or the value is a path or URL, not a reference (`./logo.png`,
//!     `https://...`, a picture file, an action's local `Dockerfile`);
//!   * in a GitHub workflow, `container: <scalar>` (the short form of `container.image`);
//!   * `uses: docker://<ref>` (a workflow step or an action's `runs`).
//!
//! A mapping-valued `image:` (a Helm values file's `repository:`/`tag:` pair) is not read: the
//! pair is joined by a template the check does not run. A file that is not YAML at all (a Helm
//! template's `{{ }}` lines) is read line by line for `image:` instead, so a literal reference
//! in a template is still judged and a templated one is unreadable, not a finding.

use saphyr::{LoadableYamlNode, MarkedYaml, Scalar, YamlData};
use std::collections::BTreeMap;

use super::reference::substitute;
use super::scan::Ref;

/// Picture suffixes: an `image:` naming one is a page's picture, not a container.
const PICTURES: [&str; 8] = ["png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp"];

fn str_of<'a>(node: &'a MarkedYaml) -> Option<&'a str> {
    match &node.data {
        YamlData::Value(Scalar::String(s)) => Some(s),
        _ => None,
    }
}

fn key_is(node: &MarkedYaml, want: &str) -> bool {
    str_of(node) == Some(want)
}

/// Whether an `image:` value names a container at all.
fn is_container_value(value: &str, action: bool) -> bool {
    let v = value.trim().trim_matches(['"', '\'']);
    if let Some(rest) = v.strip_prefix("docker://") {
        return !rest.is_empty();
    }
    if action || v.is_empty() || v.contains("://") || v.starts_with(['/', '.', '~']) {
        return false;
    }
    let last = v.rsplit('/').next().unwrap_or(v).to_ascii_lowercase();
    if last.starts_with("dockerfile") || last.starts_with("containerfile") {
        return false;
    }
    !last
        .rsplit_once('.')
        .is_some_and(|(_, ext)| PICTURES.contains(&ext))
}

/// What kind of YAML file `rel` is, for the keys only one kind gives meaning.
#[derive(Clone, Copy)]
struct Kind {
    workflow: bool,
    action: bool,
}

fn kind(rel: &str) -> Kind {
    let name = rel.rsplit('/').next().unwrap_or(rel);
    Kind {
        workflow: rel.starts_with(".github/workflows/"),
        action: name == "action.yml" || name == "action.yaml",
    }
}

fn push(out: &mut Vec<Ref>, key: &MarkedYaml, value: &MarkedYaml, raw: &str) {
    let start = key.span.start.line().max(1);
    let end = value.span.start.line().max(start);
    out.push(Ref {
        start,
        end,
        raw: substitute(raw.trim(), &BTreeMap::new()),
    });
}

fn walk(node: &MarkedYaml, k: Kind, out: &mut Vec<Ref>) {
    match &node.data {
        YamlData::Mapping(m) => {
            let builds = m.keys().any(|key| key_is(key, "build"));
            for (key, value) in m {
                if let Some(v) = str_of(value) {
                    let counts = match str_of(key) {
                        Some("image") => !builds && is_container_value(v, k.action),
                        Some("container") => k.workflow && is_container_value(v, false),
                        Some("uses") => v.trim().starts_with("docker://"),
                        _ => false,
                    };
                    if counts {
                        push(out, key, value, v);
                    }
                }
                walk(value, k, out);
            }
        }
        YamlData::Sequence(items) => {
            for item in items {
                walk(item, k, out);
            }
        }
        _ => {}
    }
}

/// A line read of `image:` keys, for a file the parser refuses.
fn line_refs(text: &str, k: Kind) -> Vec<Ref> {
    let mut out = Vec::new();
    for (i, line) in text.lines().enumerate() {
        let body = line.trim_start();
        let body = body.strip_prefix("- ").map_or(body, str::trim_start);
        let Some(value) = body.strip_prefix("image:") else {
            continue;
        };
        let value = super::scan::strip_comment(value).trim();
        if is_container_value(value, k.action) {
            out.push(Ref {
                start: i + 1,
                end: i + 1,
                raw: substitute(value, &BTreeMap::new()),
            });
        }
    }
    out
}

/// Every image the YAML file `rel` names, and whether it parsed as YAML.
#[must_use]
pub fn refs(rel: &str, text: &str) -> (Vec<Ref>, bool) {
    let k = kind(rel);
    let Ok(docs) = MarkedYaml::load_from_str(text) else {
        return (line_refs(text, k), false);
    };
    let mut out = Vec::new();
    for doc in &docs {
        walk(doc, k, &mut out);
    }
    // An alias resolves to its anchor's node: the same reference, found twice at one line.
    out.sort_by(|a, b| (a.start, &a.raw).cmp(&(b.start, &b.raw)));
    out.dedup_by(|a, b| a.start == b.start && a.raw == b.raw);
    (out, true)
}
