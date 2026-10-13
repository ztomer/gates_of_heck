//! A per-file count seed that may only fall: the debt a repo carried when it adopted a check.
//!
//! The shape two checks share (`goh fixed-sleep`, `goh status-markers`): a scan names its sites
//! per file; a JSON seed at the repo root says how many each file had at adoption. A file over its
//! count is red with its sites; a file under it is red too, until the seed is lowered (`--seed`
//! rewrites it), so a fixed site cannot leave headroom for a new one; an entry for a file gone
//! clean is stale; a corrupt seed is an error, never an empty seed. Written once, so a third check
//! of this shape inherits every direction instead of re-deriving four of them.

use goh_tui::{ERR, OK, START};
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::path::Path;

/// What one check calls itself and its seed.
pub struct Spec {
    /// The report prefix, e.g. `[fixed_sleep]`.
    pub tag: &'static str,
    /// The seed's file name, at the repo root.
    pub file: &'static str,
    /// The subject, plural, e.g. `Python file(s)`.
    pub noun: &'static str,
    /// The command a lowered seed is rewritten with, e.g. `goh fixed-sleep --seed`.
    pub reseed: &'static str,
    /// The green claim, e.g. `no new fixed sleep stands in for a readiness signal`.
    pub clean: &'static str,
    /// The seed file's own `why`, so the file explains itself to whoever opens it.
    pub why: &'static str,
    /// The subject's floor key (`crate::subject`), read from `.gatesrc` by [`run`].
    pub subject: &'static crate::subject::Subject,
}

/// The findings per file the scan read, and the files it could not parse.
#[derive(Debug)]
pub struct Scanned<F> {
    /// Subject files the repo lists, before any exclusion.
    pub listed: usize,
    pub read: usize,
    /// The floor `.gatesrc` declares for `read` (the spec's subject key), if any.
    pub floor: Option<usize>,
    pub found: BTreeMap<String, Vec<F>>,
    pub unreadable: Vec<(String, String)>,
}

impl<F> Default for Scanned<F> {
    fn default() -> Self {
        Self {
            listed: 0,
            read: 0,
            floor: None,
            found: BTreeMap::new(),
            unreadable: Vec::new(),
        }
    }
}

/// The verdict over a scan and a seed: (exit code, report). `site` renders one finding, after
/// `path:`. Pure, so every direction is a unit test.
#[must_use]
pub fn judge<F>(
    spec: &Spec,
    scanned: &Scanned<F>,
    seed: &BTreeMap<String, usize>,
    site: impl Fn(&F) -> String,
) -> (i32, String) {
    let (tag, noun) = (spec.tag, spec.noun);
    let mut s = String::new();
    if scanned.listed > 0 && scanned.read == 0 {
        let _ = writeln!(
            s,
            "{ERR} {tag} this repo lists {} {noun} and the check read NONE of them: an exclusion \
             that removes the whole subject passes over nothing -- narrow it",
            scanned.listed
        );
        return (1, s);
    }
    // Under its floor, the stale-seed verdict is withheld: a blind scan finds nothing, so every
    // seeded file would read as fixed and the advice would be to lower the seed -- the blindness
    // laundered into the ratchet (`crate::subject`, the pairing).
    if let Some(text) = crate::subject::below(spec.subject, scanned.floor, scanned.read) {
        s.push_str(&text);
        return (1, s);
    }
    let mut red = false;
    for (path, why) in &scanned.unreadable {
        let _ = writeln!(
            s,
            "{ERR} {tag} {path} is not a file this check can read: {why}"
        );
        red = true;
    }
    for (path, sites) in &scanned.found {
        let allowed = seed.get(path).copied().unwrap_or(0);
        if sites.len() > allowed {
            red = true;
            for f in sites {
                let _ = writeln!(s, "{ERR} {tag} {path}:{}", site(f));
            }
            if allowed > 0 {
                let _ = writeln!(
                    s,
                    "  ({path} is seeded with {allowed}; it has {})",
                    sites.len()
                );
            }
        }
    }
    for (path, &allowed) in seed {
        let has = scanned.found.get(path).map_or(0, Vec::len);
        if has < allowed {
            red = true;
            let _ = writeln!(
                s,
                "{ERR} {tag} {path} is seeded with {allowed} and has {has}: lower the seed \
                 ({}), so a fixed site leaves no room for a new one",
                spec.reseed
            );
        }
    }
    let total: usize = scanned.found.values().map(Vec::len).sum();
    if red {
        let _ = writeln!(s, "{START} {tag} read {} {noun}", scanned.read);
        return (1, s);
    }
    let _ = writeln!(
        s,
        "{OK} {tag} {}: {} {noun} read, {total} seeded",
        spec.clean, scanned.read
    );
    (0, s)
}

/// Read the seed: absent is empty; malformed is an ERROR, never an empty seed -- a gate must
/// not be able to switch itself off by corrupting its own exemptions.
///
/// # Errors
///
/// What is wrong with the file.
pub fn load_seed(spec: &Spec, root: &Path) -> Result<BTreeMap<String, usize>, String> {
    let file = spec.file;
    let text = match std::fs::read_to_string(root.join(file)) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(BTreeMap::new()),
        Err(e) => return Err(format!("cannot read {file}: {e}")),
    };
    let value: serde_json::Value =
        serde_json::from_str(&text).map_err(|e| format!("{file} is not JSON: {e}"))?;
    let files = value
        .get("files")
        .and_then(serde_json::Value::as_object)
        .ok_or_else(|| format!("{file} has no \"files\" object"))?;
    files
        .iter()
        .map(|(k, v)| {
            v.as_u64()
                .and_then(|n| usize::try_from(n).ok())
                .map(|n| (k.clone(), n))
                .ok_or_else(|| format!("{file}: {k}'s count is not a whole number"))
        })
        .collect()
}

/// The seed a scan implies: every file with a finding, and its count.
#[must_use]
pub fn seed_text<F>(spec: &Spec, scanned: &Scanned<F>) -> String {
    let files: serde_json::Map<String, serde_json::Value> = scanned
        .found
        .iter()
        .map(|(k, v)| (k.clone(), serde_json::Value::from(v.len())))
        .collect();
    let doc = serde_json::json!({ "why": spec.why, "files": files });
    serde_json::to_string_pretty(&doc).unwrap_or_default() + "\n"
}

/// Scan the subject files among `files` (repo-relative) under `root`. A file that is not UTF-8
/// text is not read and not counted.
#[must_use]
pub fn scan_files<F>(
    root: &Path,
    files: &[String],
    subject: impl Fn(&str) -> bool,
    scan: impl Fn(&str) -> Result<Vec<F>, String>,
) -> Scanned<F> {
    let mut out = Scanned::default();
    for rel in files.iter().filter(|f| subject(f)) {
        let Ok(text) = std::fs::read_to_string(root.join(rel)) else {
            continue;
        };
        out.read += 1;
        match scan(&text) {
            Ok(found) if !found.is_empty() => {
                out.found.insert(rel.clone(), found);
            }
            Ok(_) => {}
            Err(why) => out.unreadable.push((rel.clone(), why)),
        }
    }
    out
}

/// Why a scan must not be written as the seed, if it must not: it read none of a subject the repo
/// lists, or less than its floor. Either would write a blind scan's empty findings as the new debt.
#[must_use]
pub fn refuse_seed<F>(spec: &Spec, scanned: &Scanned<F>) -> Option<String> {
    let tag = spec.tag;
    if scanned.listed > 0 && scanned.read == 0 {
        return Some(format!(
            "{ERR} {tag} refusing to seed: the repo lists {} {} and the scan read NONE of them\n",
            scanned.listed, spec.noun
        ));
    }
    crate::subject::below(spec.subject, scanned.floor, scanned.read)
        .map(|text| text + &format!("{ERR} {tag} refusing to seed a scan under its floor\n"))
}

/// The command over the working tree's listed files: `--seed` writes the seed, else the verdict.
#[must_use]
pub fn run<F>(
    spec: &Spec,
    seed: bool,
    exclude: &str,
    subject: impl Fn(&str) -> bool,
    scan: impl Fn(&str) -> Result<Vec<F>, String>,
    site: impl Fn(&F) -> String,
) -> i32 {
    let tag = spec.tag;
    let root = crate::gitutil::repo_root().map_or_else(
        || std::env::current_dir().unwrap_or_default(),
        std::path::PathBuf::from,
    );
    let listed = match crate::gitutil::listed_files(&root, false) {
        Ok(l) => l,
        Err(m) => {
            goh_tui::eoutln!("{ERR} {tag} {m}");
            return 2;
        }
    };
    let filter = match crate::steps::compile_exclude(exclude) {
        Ok(f) => f,
        Err(m) => {
            goh_tui::eoutln!("{ERR} {tag} {m}");
            return 2;
        }
    };
    let subject_listed = listed.iter().filter(|f| subject(f)).count();
    let files: Vec<String> = listed
        .into_iter()
        .filter(|f| !filter.as_ref().is_some_and(|x| x.is_match(f)))
        .collect();
    let floor = match crate::gatesrc::load(&root) {
        Ok(cfg) => crate::subject::floor(&cfg, spec.subject),
        Err(m) => {
            goh_tui::eoutln!("{ERR} {tag} {m}");
            return 2;
        }
    };
    let scanned = Scanned {
        listed: subject_listed,
        floor,
        ..scan_files(&root, &files, subject, scan)
    };
    if seed {
        if let Some(text) = refuse_seed(spec, &scanned) {
            goh_tui::eout(&text);
            return 1;
        }
        if let Err(e) = std::fs::write(root.join(spec.file), seed_text(spec, &scanned)) {
            goh_tui::eoutln!("{ERR} {tag} cannot write {}: {e}", spec.file);
            return 2;
        }
        let total: usize = scanned.found.values().map(Vec::len).sum();
        goh_tui::outln!(
            "{OK} {tag} seeded {}: {total} site(s) in {} file(s), of {} read",
            spec.file,
            scanned.found.len(),
            scanned.read
        );
        return 0;
    }
    let seeded = match load_seed(spec, &root) {
        Ok(s) => s,
        Err(m) => {
            goh_tui::eoutln!("{ERR} {tag} {m}");
            return 2;
        }
    };
    let (code, text) = judge(spec, &scanned, &seeded, site);
    goh_tui::out(&text);
    code
}
