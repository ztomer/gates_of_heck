//! The carried hook index is read only by `goh_bind_hook_index` (`goh bare-hook-index`).
//!
//! A commit hook carries the index being committed as `GOH_HOOK_INDEX_FILE`, beside
//! `GOH_HOOK_GIT_DIR`, the repository it belongs to (`gates/_git_env.sh`, contract #12), because
//! `commit -a` commits a temporary index only `GIT_INDEX_FILE` names. Both are exported, so every
//! process the hook starts inherits them -- including a gate that a consumer's TEST runs on a
//! fixture repository. `goh_bind_hook_index` binds the index only inside the repository it came
//! from. A script that reads the variable bare binds it anywhere: routines' and `media_server`'s
//! tools/gate.sh did `GIT_INDEX_FILE="${GOH_HOOK_INDEX_FILE:-...}" git diff --cached`, and under a
//! `commit -a` a `media_server` test's fixture gate judged `media_server`'s index and died
//! "unable to read 404acec" (2026-10-08, found by the servers session).
//!
//! The rule: no shell source expands either variable (`scan.rs` says what an expansion is) except
//! the one that DEFINES `goh_bind_hook_index`. Scope: every shell source (`crate::shellsrc`).
//!
//! The opposite mistake is the commoner one: a HOOK-RUN script that reads an index without binding
//! the carried one at all (`index.rs`), so under `git commit -- path` it judges the repository's own
//! index, not the tree being committed (.loop's queue gate, LOOP.COMMITSLOW, GOH.BAREINDEX).
//!
//! WIRING: a structural step in EVERY repo, a hard gate at both scopes for expansions (the sweep
//! found two sites, both fixed in their own repos). Unbound index reads are judged against the
//! estate seed (`seed.rs`, `hook_index_seed.json`): `koffee_big`'s two sites WARN until fixed there;
//! any other is red.

pub mod index;
pub mod scan;
pub mod seed;

use goh_tui::{ERR, OK, WARN};
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::path::Path;

const TAG: &str = "[no_bare_hook_index]";

fn clip(s: &str) -> String {
    let s = s.trim();
    let mut out: String = s.chars().take(80).collect();
    if out.len() < s.len() {
        out.push_str("...");
    }
    out
}

/// The unbound index reads in the hook-run sources among `sources`, per file:
/// `(path -> sites)`. The reach is read from every tracked shell source in the worktree (a
/// changed library is hook-run because an UNCHANGED gate names it); the judged text is `sources`'.
fn index_findings(
    root: &Path,
    sources: &[(String, String)],
    exclude: Option<&crate::pathfilter::PathFilter>,
) -> BTreeMap<String, Vec<index::IndexRead>> {
    let tracked = crate::gitutil::index_files(root).unwrap_or_default();
    let graph = crate::shellsrc::sources(root, &tracked, exclude, false);
    let reached = index::hook_run(&graph);
    sources
        .iter()
        .filter(|(rel, text)| reached.contains(rel) && !scan::defines_binder(text))
        .map(|(rel, text)| (rel.clone(), index::unbound_reads(text)))
        .filter(|(_, r)| !r.is_empty())
        .collect()
}

const EXPANSION_FIX: &str = "    A hook carries the index being committed as GOH_HOOK_INDEX_FILE, beside the repository\n    \
     it belongs to (GOH_HOOK_GIT_DIR). Read bare, a gate run on ANOTHER repository -- a test's\n    \
     fixture -- binds that index there and judges the wrong tree. Bind it with\n    \
     goh_bind_hook_index (`. \"$GOH_DIR/gates/_git_env.sh\"`), which binds only inside the\n    \
     repository it came from; for one call, in a subshell:\n    \
     ( goh_bind_hook_index; git diff --cached ... ). Contract #12.\n";

const INDEX_FIX: &str = "    The commit hook drops GIT_INDEX_FILE and carries it as GOH_HOOK_INDEX_FILE: under\n    \
     `git commit -- path` or `commit -a` the tree being committed is a TEMPORARY index, and a\n    \
     plain read judges the repository's own index instead (.loop skipped its queue gate on every\n    \
     tick commit, LOOP.COMMITSLOW). Bind it first -- `. \"$GOH_DIR/gates/_git_env.sh\"` then\n    \
     `( goh_bind_hook_index; git diff --cached ... )`; outside a hook it binds nothing. Contract #12.\n";

/// The verdict over `files`: `(exit code, stdout, stderr)`. `seed` is the estate's seeded unbound
/// index reads (`seed.rs`); expansions of the carried variables are never seeded.
fn judge(
    root: &Path,
    files: &[String],
    exclude: &str,
    staged: bool,
    seed: &seed::Seed,
) -> (i32, String, String) {
    let exclude = match crate::steps::compile_exclude(exclude) {
        Ok(x) => x,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let sources = crate::shellsrc::sources(root, files, exclude.as_ref(), staged);
    crate::subject::record(&crate::subject::BARE_HOOK_INDEX, sources.len());
    let scope = if staged { "staged" } else { "tracked" };
    let lines_of = |rel: &str| -> Vec<String> {
        sources
            .iter()
            .find(|(r, _)| r == rel)
            .map(|(_, t)| t.lines().map(str::to_string).collect())
            .unwrap_or_default()
    };
    let (listing, n) = expansions(&sources);
    let found = index_findings(root, &sources, exclude.as_ref());
    let counts: BTreeMap<String, usize> = found.iter().map(|(p, r)| (p.clone(), r.len())).collect();
    // One git spawn, and only when a seed could matter: findings to judge, or a seed to judge
    // stale (a full run) or to settle (a commit's own run, GOH.SEEDSTALE).
    let repo = crate::estate_seed::repo_identity(root, !counts.is_empty(), seed);
    let run = crate::estate_seed::Scope::of(staged);
    let (verdict, settled) =
        match crate::estate_seed::judge_settled(root, &repo, run, &counts, seed, seed::KIND) {
            Ok(j) => j,
            Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
        };
    let site = |rel: &str, r: &index::IndexRead| {
        let lines = lines_of(rel);
        let src = lines.get(r.line - 1).map_or("", String::as_str);
        format!(
            "    {rel}:{}: {}  -- {} without goh_bind_hook_index",
            r.line,
            clip(src),
            r.what
        )
    };
    let mut index_listing = String::new();
    let mut m = 0usize;
    for rel in &verdict.red {
        for r in &found[rel] {
            m += 1;
            let _ = writeln!(index_listing, "{}", site(rel, r));
        }
    }
    let mut warns = String::new();
    for (rel, note) in &verdict.warn {
        let _ = writeln!(
            warns,
            "{WARN} {TAG} seeded (GOH.BAREINDEX ratchet): {rel}: {note}"
        );
        for r in found.get(rel).into_iter().flatten() {
            let _ = writeln!(warns, "{}", site(rel, r));
        }
    }
    let stale = crate::estate_seed::stale_report(TAG, "GOH.BAREINDEX", &verdict.stale);
    if n == 0 && m == 0 && stale.is_empty() {
        warns.push_str(&crate::estate_seed::settle_store(
            &repo,
            seed::KIND,
            &settled,
            TAG,
            "GOH.BAREINDEX",
        ));
        let ok = format!(
            "{OK} {TAG} OK — {} {scope} shell source(s), the hook's index read only by goh_bind_hook_index\n",
            sources.len()
        );
        return (0, ok, warns);
    }
    let mut err = warns;
    if n > 0 {
        let _ = writeln!(
            err,
            "{ERR} {TAG} {n} read(s) of the hook's carried index outside goh_bind_hook_index ({scope}):"
        );
        err.push_str(&listing);
        err.push_str(EXPANSION_FIX);
    }
    if m > 0 {
        let _ = writeln!(
            err,
            "{ERR} {TAG} {m} index read(s) in a hook-run script that never binds the hook's index ({scope}):"
        );
        err.push_str(&index_listing);
        err.push_str(INDEX_FIX);
    }
    err.push_str(&stale);
    (1, String::new(), err)
}

/// The listing of the carried index's expansions outside the binder, and how many there are.
fn expansions(sources: &[(String, String)]) -> (String, usize) {
    let mut listing = String::new();
    let mut n = 0usize;
    for (rel, text) in sources {
        if scan::defines_binder(text) {
            continue;
        }
        let lines: Vec<&str> = text.lines().collect();
        for r in scan::reads(text) {
            n += 1;
            let src = lines.get(r.line - 1).copied().unwrap_or("");
            let _ = writeln!(
                listing,
                "    {rel}:{}: {}  -- expands ${}",
                r.line,
                clip(src),
                r.var
            );
        }
    }
    (listing, n)
}

/// The estate seed for the repository at `repo`, or the report a corrupt or foreign one makes.
fn estate_seed(repo: &Path) -> Result<seed::Seed, String> {
    seed::load(repo).map_err(|m| format!("{ERR} {TAG} {m}\n"))
}

/// `goh bare-hook-index [--staged] [--exclude RE]`.
#[must_use]
pub fn run_command(staged: bool, exclude: &str) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        goh_tui::eoutln!("{ERR} {TAG} not a git repository");
        return 2;
    };
    let files = match crate::gitutil::listed_files(&root, staged) {
        Ok(f) => f,
        Err(m) => {
            goh_tui::eoutln!("{ERR} {TAG} {m}");
            return 2;
        }
    };
    let (code, out, err) = match estate_seed(&root) {
        Ok(seed) => judge(&root, &files, exclude, staged, &seed),
        Err(m) => (2, String::new(), m),
    };
    goh_tui::out(&out);
    goh_tui::eout(&err);
    code
}

/// The structural step, in every repo.
#[must_use]
pub fn step(
    repo: &Path,
    files: &[String],
    cfg: &crate::gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    let label = if staged {
        "no bare read of the hook's index (staged)"
    } else {
        "no bare read of the hook's index"
    };
    let start = crate::step_report::begin(label);
    let verdict = match estate_seed(repo) {
        Ok(seed) => judge(repo, files, &cfg.exclude, staged, &seed),
        Err(m) => (2, String::new(), m),
    };
    match verdict {
        (0, _, warns) => {
            goh_tui::eout(&warns);
            crate::step_report::ok(label, start);
            None
        }
        (code, _, err) => {
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/hookindex/mod.rs"),
                &err,
                start,
            );
            Some(code)
        }
    }
}

#[cfg(test)]
mod tests;
