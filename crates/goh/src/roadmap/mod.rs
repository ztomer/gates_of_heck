//! `goh roadmap`: a repo's roadmap is ONE forward-looking file of open entries (GOH.ROADMAPGATE).
//!
//! The 2026-11-08 roadmap audit found every way a plan rots: completed rows left in place, two
//! plan files saying different things, entries the umbrella queue had already closed. The rework
//! fixed the instances; this is the class. Four rules, over the file `.gatesrc` names as
//! `GOH_ROADMAP` (`koffee_big` carries `FEATURE_PLAN.md`, so the choice is written, not guessed):
//!
//!   1. EXACTLY ONE plan file: any other `ROADMAP`/`BACKLOG`/`TODO`/`PLAN` `.md` (`scan::is_plan_file`)
//!      is a finding, and a declared file that is not there is red outright;
//!   2. no completed entries (`DONE`, `shipped`, `~~`, `✓ **`, a ticked box): git and the CHANGELOG
//!      keep completed work (house rule #16);
//!   3. every open entry under a `Phase` heading opens with an id and carries a `Done when:`; a
//!      clause outside every entry is a finding (the anchor that keeps the entry grammar honest);
//!   4. an entry citing `(queue: <ID>)` whose umbrella item is done is a finding (`ZoneWM`'s advisory
//!      `tools/check_stale_backlog.py`, generalised; the queue is a seam, `queue.rs`).
//!
//! Today's findings are DEBT held by `roadmap_seed.json` (`judge.rs`): a count per `<file>#<rule>`
//! that may only fall. `GOH_ROADMAP` unset is red (GOH.ROADMAPREQ), save for a repo still named in
//! `pending.txt`, the staged turn-on, which passes with a warning naming the item;
//! `GOH_ROADMAP=none` declares no roadmap, so every plan file is a finding.
//!
//! A structural step in every repo. At `--staged` it judges only a commit that brings the
//! roadmap, its seed, `.gatesrc` or a plan file -- the queue moves under a repo, and a commit
//! about something else is not the place to bill it; the push (`--full`) judges every rule.

pub mod judge;
pub mod queue;
pub mod scan;

use goh_tui::ERR;
use std::collections::BTreeMap;
use std::path::Path;

use judge::Gathered;

/// The report prefix.
pub const TAG: &str = "[roadmap]";
/// The seed's file name, at the repo root.
pub const SEED_FILE: &str = "roadmap_seed.json";
/// The step's label, as `structural_steps.txt` names it.
pub const LABEL: &str = "one roadmap, open entries only";

/// What `.gatesrc` declares.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Declared {
    /// `GOH_ROADMAP` unset: nothing is judged, and the gate says so.
    Unset,
    /// `GOH_ROADMAP=none`: this repo keeps no roadmap.
    NoRoadmap,
    /// The one roadmap, repo-relative.
    File(String),
}

impl Declared {
    #[must_use]
    pub fn from_cfg(cfg: &crate::gatesrc::Gatesrc) -> Self {
        match cfg.roadmap.as_deref() {
            None => Self::Unset,
            Some("none") => Self::NoRoadmap,
            Some(p) => Self::File(p.trim_start_matches("./").to_owned()),
        }
    }
}

/// Read every rule over `tree` (repo-relative, the scope's listing). `queue` is asked only when
/// the roadmap cites the queue.
pub fn gather(
    root: &Path,
    tree: &[String],
    exclude: Option<&crate::pathfilter::PathFilter>,
    declared: &Declared,
    staged: bool,
    queue: impl FnOnce() -> Result<BTreeMap<String, String>, String>,
) -> Gathered {
    let roadmap = match declared {
        Declared::File(p) => p.clone(),
        Declared::NoRoadmap | Declared::Unset => "(none)".to_owned(),
    };
    let mut g = Gathered {
        roadmap: roadmap.clone(),
        ..Gathered::default()
    };
    for rel in tree {
        if *rel == roadmap || !scan::is_plan_file(rel) || exclude.is_some_and(|x| x.is_match(rel)) {
            continue;
        }
        g.found
            .entry(format!("{rel}#{}", scan::Rule::Extra.key()))
            .or_default()
            .push(format!(
                "{rel}:1: a second plan file beside GOH_ROADMAP={roadmap}: fold its open work into \
                 the one roadmap and delete it (house rule #16)"
            ));
    }
    if !matches!(declared, Declared::File(_)) {
        return g;
    }
    let text = tree
        .contains(&roadmap)
        .then(|| crate::gitutil::content_bytes(root, &roadmap, staged))
        .flatten();
    let Some(text) = text else {
        g.hard.push(format!(
            "GOH_ROADMAP={roadmap} is not a tracked file here: name the roadmap this repo keeps, \
             or GOH_ROADMAP=none"
        ));
        return g;
    };
    let read = scan::read(&String::from_utf8_lossy(&text));
    g.entries = read.entries.len();
    g.phases = read.phases;
    let mut findings = read.findings;
    if !read.citations.is_empty() {
        match queue() {
            Ok(statuses) => {
                for (line, id) in &read.citations {
                    match statuses.get(id).map(String::as_str) {
                        Some("done") => findings.push(scan::Finding {
                            rule: scan::Rule::QueueDone,
                            line: *line,
                            what: format!(
                                "(queue: {id}) is done in the umbrella queue: prune the entry"
                            ),
                        }),
                        Some(_) => {}
                        None => g.notes.push(format!(
                            "{roadmap}:{line}: (queue: {id}) names no item in the umbrella queue"
                        )),
                    }
                }
            }
            Err(why) => {
                g.queue_withheld = true;
                g.notes.push(format!(
                    "rule 4 (a cited queue item is done) NOT JUDGED: {why}; set {} or pass --queue",
                    queue::ENV
                ));
            }
        }
    }
    for f in findings {
        g.found
            .entry(format!("{roadmap}#{}", f.rule.key()))
            .or_default()
            .push(format!("{roadmap}:{}: {}", f.line, f.what));
    }
    g
}

/// The seed at this scope (the index at `--staged`) and the one HEAD carries.
type Seeds = (BTreeMap<String, usize>, Option<BTreeMap<String, usize>>);

fn seeds(root: &Path, staged: bool) -> Result<Seeds, String> {
    let now = crate::gitutil::content_bytes(root, SEED_FILE, staged)
        .map(|b| judge::parse_seed(&String::from_utf8_lossy(&b)))
        .transpose()?
        .unwrap_or_default();
    let head = crate::gitutil::committed_bytes(root, SEED_FILE)
        .and_then(|b| judge::parse_seed(&String::from_utf8_lossy(&b)).ok());
    Ok((now, head))
}

/// The repos still allowed an unset `GOH_ROADMAP` (GOH.ROADMAPREQ's staged turn-on).
const PENDING: &str = include_str!("pending.txt");

/// Whether this repo is still pending: its root commit (`gitutil::root_commit`, which an export
/// answers too) or its checkout's basename names a line, or a line is `*`. `GOH_ROADMAP_PENDING`
/// names another list: a SEAM for tests, read from the environment, never from `.gatesrc`.
fn pending(root: &Path) -> Result<bool, String> {
    let text = match std::env::var_os("GOH_ROADMAP_PENDING") {
        Some(p) => std::fs::read_to_string(&p)
            .map_err(|e| format!("GOH_ROADMAP_PENDING={}: {e}", Path::new(&p).display()))?,
        None => PENDING.to_owned(),
    };
    let name = root
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or_default();
    let commit = crate::gitutil::root_commit(root);
    Ok(text
        .lines()
        .map(|l| l.split('#').next().unwrap_or_default().trim())
        .filter(|l| !l.is_empty())
        .any(|l| {
            l == "*"
                || l.split_whitespace()
                    .any(|w| w == name || commit.as_deref() == Some(w))
        }))
}

/// The plan files a repo with no `GOH_ROADMAP` holds, for the line that says it is unset.
fn unset_note(tree: &[String]) -> String {
    let plans: Vec<&str> = tree
        .iter()
        .filter(|r| scan::is_plan_file(r))
        .map(String::as_str)
        .collect();
    let held = if plans.is_empty() {
        "no plan file".to_owned()
    } else {
        plans.join(", ")
    };
    format!(
        "GOH_ROADMAP is unset, so no roadmap rule is judged here: name the one roadmap in .gatesrc \
         (or GOH_ROADMAP=none) and run goh roadmap --seed. Plan files here: {held}"
    )
}

/// The verdict for a repo: (exit code, report).
fn verdict(
    root: &Path,
    cfg: &crate::gatesrc::Gatesrc,
    staged: bool,
    queue_flag: Option<&str>,
) -> (i32, String) {
    let tree = if staged {
        crate::gitutil::index_files(root)
    } else {
        crate::gitutil::listed_files(root, false)
    };
    let tree = match tree {
        Ok(t) => t,
        Err(m) => return (2, format!("{ERR} {TAG} {m}\n")),
    };
    let declared = Declared::from_cfg(cfg);
    if declared == Declared::Unset {
        return match pending(root) {
            Ok(true) => (
                0,
                format!(
                    "{} {TAG} {} (this repo is pending in goh's roadmap/pending.txt, GOH.ROADMAPREQ: \
                     it goes red once that line is gone)\n",
                    goh_tui::WARN,
                    unset_note(&tree)
                ),
            ),
            Ok(false) => (1, format!("{ERR} {TAG} {}\n", unset_note(&tree))),
            Err(m) => (2, format!("{ERR} {TAG} {m}\n")),
        };
    }
    let exclude = match crate::steps::compile_exclude(&cfg.exclude) {
        Ok(x) => x,
        Err(m) => return (2, format!("{ERR} {TAG} {m}\n")),
    };
    let g = gather(root, &tree, exclude.as_ref(), &declared, staged, || {
        queue::load(queue_flag)
    });
    match seeds(root, staged) {
        Ok((now, head)) => judge::judge(&g, &now, head.as_ref()),
        Err(m) => (2, format!("{ERR} {TAG} {m}\n")),
    }
}

/// `goh roadmap [--staged] [--seed] [--queue PATH]`.
#[must_use]
pub fn run_command(staged: bool, seed: bool, queue_flag: Option<&str>) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        goh_tui::eoutln!("{ERR} {TAG} not a git repository");
        return 2;
    };
    let cfg = match crate::gatesrc::load(&root) {
        Ok(c) => c,
        Err(m) => {
            goh_tui::eoutln!("{ERR} {TAG} {m}");
            return 2;
        }
    };
    if seed {
        return write_seed(&root, &cfg, queue_flag);
    }
    let (code, text) = verdict(&root, &cfg, staged, queue_flag);
    if code == 0 {
        goh_tui::out(&text);
    } else {
        goh_tui::eout(&text);
    }
    code
}

/// `--seed`: write today's findings, refused if a key would rise against the seed in the tree.
fn write_seed(root: &Path, cfg: &crate::gatesrc::Gatesrc, queue_flag: Option<&str>) -> i32 {
    let declared = Declared::from_cfg(cfg);
    if declared == Declared::Unset {
        goh_tui::eoutln!("{ERR} {TAG} refusing to seed: GOH_ROADMAP is unset in .gatesrc");
        return 2;
    }
    let (tree, exclude) = match (
        crate::gitutil::listed_files(root, false),
        crate::steps::compile_exclude(&cfg.exclude),
    ) {
        (Ok(t), Ok(x)) => (t, x),
        (Err(m), _) | (_, Err(m)) => {
            goh_tui::eoutln!("{ERR} {TAG} {m}");
            return 2;
        }
    };
    let g = gather(root, &tree, exclude.as_ref(), &declared, false, || {
        queue::load(queue_flag)
    });
    let old = match seeds(root, false) {
        Ok((now, _)) => root.join(SEED_FILE).exists().then_some(now),
        Err(m) => {
            goh_tui::eoutln!("{ERR} {TAG} {m}");
            return 2;
        }
    };
    let text = match judge::seed_text(&g, old.as_ref()) {
        Ok(t) => t,
        Err(m) => {
            goh_tui::eoutln!("{ERR} {TAG} {m}");
            return 1;
        }
    };
    if let Err(e) = std::fs::write(root.join(SEED_FILE), text) {
        goh_tui::eoutln!("{ERR} {TAG} cannot write {SEED_FILE}: {e}");
        return 2;
    }
    for note in &g.notes {
        goh_tui::outln!("{} {TAG} {note}", goh_tui::WARN);
    }
    let total: usize = g.found.values().map(Vec::len).sum();
    goh_tui::outln!(
        "{} {TAG} seeded {SEED_FILE}: {} under {}",
        goh_tui::OK,
        goh_tui::counted(total, "finding"),
        goh_tui::counted(g.found.len(), "key")
    );
    0
}

/// Does this commit bring anything the roadmap rules read?
fn commit_touches(files: &[String], cfg: &crate::gatesrc::Gatesrc) -> bool {
    let roadmap = cfg.roadmap.as_deref().unwrap_or_default();
    files
        .iter()
        .any(|f| f == roadmap || f == SEED_FILE || f == ".gatesrc" || scan::is_plan_file(f))
}

/// The structural step, in every repo.
#[must_use]
pub fn step(
    repo: &Path,
    files: &[String],
    cfg: &crate::gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    let start = crate::step_report::begin(LABEL);
    if staged && !commit_touches(files, cfg) {
        crate::step_report::ok(LABEL, start);
        return None;
    }
    let (code, text) = verdict(repo, cfg, staged, None);
    if code == 0 {
        goh_tui::out(&text);
        crate::step_report::ok(LABEL, start);
        return None;
    }
    let _ = crate::step_report::fail(
        LABEL,
        &crate::step_report::ported("crates/goh/src/roadmap/mod.rs"),
        &text,
        start,
    );
    Some(code)
}

#[cfg(test)]
mod tests;
