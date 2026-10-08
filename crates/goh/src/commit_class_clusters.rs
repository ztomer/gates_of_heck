//! `goh commit-class --clusters REV...`: the fixes in a range, clustered by Class AND by the files
//! they touched -- each cluster a hardening candidate (BACKLOG 2.1).
//!
//! The commit gate sees a class only when its words repeat. A loop-start audit can also see the
//! repeats committed without a Class, and the paraphrases whose fixes landed in the same files.
//! Two fixes link when their classes are one (`same_class`, the repo's vocabulary aside) or when
//! they share `FILE_LINK` files; a cluster is a connected group. Calibrated on `ZoneWM`'s
//! `214f0cf7..` and its author's six labelled clusters (2026-10-08): words alone keep 1 whole
//! (ceilings) and two members each of 2 more; with files, the pictures and the teardown pair join
//! too. Cluster 5 and the third members of 1 and 3 are paraphrase whose fixes share no file --
//! the ceiling of any measure of words and paths. Over the whole range it also groups repeats the
//! author did not label, among them a class committed twice in identical words.

use std::collections::{BTreeMap, BTreeSet};

/// Shared files that link two fixes (2 linked 25 of `ZoneWM`'s 75 classes into one group).
const FILE_LINK: usize = 3;
/// A file touched by more than a quarter of the range (a ROADMAP, a CHANGELOG) links nothing...
const COMMON_SHARE: usize = 4;
/// ...in a range of at least this many commits; in a short push every file looks common.
const COMMON_FLOOR: usize = 20;
/// A commit touching more than this many files links by none of them: one 47-file sweep
/// (`170694f9`, citations fixed across the tree) chained 7 unrelated classes together.
const SWEEP: usize = 20;

/// One commit of the range.
#[derive(Debug, Clone)]
pub struct Commit {
    pub sha: String,
    pub subject: String,
    pub class: Option<String>,
    pub files: BTreeSet<String>,
}

/// Whether two fixes are one: their classes are, or they share `FILE_LINK` linking files.
fn link(
    a: &Commit,
    b: &Commit,
    vocabulary: &BTreeSet<String>,
    linking: &dyn Fn(&Commit) -> BTreeSet<String>,
) -> bool {
    if let (Some(x), Some(y)) = (&a.class, &b.class) {
        if super::same_class(x, y, vocabulary) {
            return true;
        }
    }
    linking(a).intersection(&linking(b)).count() >= FILE_LINK
}

const fn root(parent: &mut [usize], mut i: usize) -> usize {
    while parent[i] != i {
        parent[i] = parent[parent[i]];
        i = parent[i];
    }
    i
}

/// The groups of two or more fixes, each in range order, ordered by their first commit.
/// `commits` is the whole range: the common files are counted over all of it, and only the
/// fixes (a Class, or a `fix:`/`perf:` subject) are clustered.
#[must_use]
pub fn clusters(commits: &[Commit], vocabulary: &BTreeSet<String>) -> Vec<Vec<usize>> {
    let mut seen: BTreeMap<&str, usize> = BTreeMap::new();
    for c in commits {
        for f in &c.files {
            *seen.entry(f.as_str()).or_default() += 1;
        }
    }
    let common: BTreeSet<&str> = seen
        .into_iter()
        .filter(|(_, n)| commits.len() >= COMMON_FLOOR && n * COMMON_SHARE > commits.len())
        .map(|(f, _)| f)
        .collect();
    let linking = |c: &Commit| -> BTreeSet<String> {
        let own: BTreeSet<String> = c
            .files
            .iter()
            .filter(|f| !common.contains(f.as_str()))
            .cloned()
            .collect();
        if own.len() > SWEEP {
            BTreeSet::new()
        } else {
            own
        }
    };
    let fixes: Vec<usize> = (0..commits.len())
        .filter(|&i| commits[i].class.is_some() || super::gated(&commits[i].subject))
        .collect();
    let mut parent: Vec<usize> = (0..commits.len()).collect();
    for (n, &i) in fixes.iter().enumerate() {
        for &j in &fixes[n + 1..] {
            if link(&commits[i], &commits[j], vocabulary, &linking) {
                let (ri, rj) = (root(&mut parent, i), root(&mut parent, j));
                parent[ri.max(rj)] = ri.min(rj);
            }
        }
    }
    let mut groups: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
    for &i in &fixes {
        groups.entry(root(&mut parent, i)).or_default().push(i);
    }
    groups.into_values().filter(|g| g.len() > 1).collect()
}

/// `git log --name-only REV...`, oldest first.
fn range(revs: &[String]) -> Result<Vec<Commit>, String> {
    let mut args = vec![
        "log",
        "--reverse",
        "--name-only",
        "--format=%x02%H%x00%s%x00%B%x01",
    ];
    args.extend(revs.iter().map(String::as_str));
    let out = super::git(&args)?;
    Ok(String::from_utf8_lossy(&out)
        .split('\u{2}')
        .filter_map(|chunk| {
            let (head, names) = chunk.split_once('\u{1}')?;
            let mut parts = head.splitn(3, '\0');
            let (sha, subject, body) = (parts.next()?, parts.next()?, parts.next()?);
            Some(Commit {
                sha: sha.trim().to_owned(),
                subject: subject.to_owned(),
                class: super::class_of(body),
                files: names
                    .lines()
                    .filter(|l| !l.trim().is_empty())
                    .map(str::to_owned)
                    .collect(),
            })
        })
        .collect())
}

/// Print the range's clusters, a hardening candidate each; exit 0, or 2 when git refuses.
#[must_use]
pub fn report(revs: &[String]) -> i32 {
    let read = range(revs).and_then(|commits| {
        let before = match commits.first() {
            Some(c) => super::classes_from(&format!("{}^", c.sha))?,
            None => Vec::new(),
        };
        Ok((commits, before))
    });
    let (commits, before) = match read {
        Ok(r) => r,
        Err(e) => {
            eprintln!("✗ [commit_class] {e}");
            return 2;
        }
    };
    let whole: Vec<String> = before
        .into_iter()
        .chain(commits.iter().filter_map(|c| c.class.clone()))
        .collect();
    let groups = clusters(&commits, &super::frequent(&whole));
    for (n, group) in groups.iter().enumerate() {
        println!(
            "→ [commit_class] cluster {} ({} fixes): a hardening candidate",
            n + 1,
            group.len()
        );
        for &i in group {
            let c = &commits[i];
            let class = c.class.as_deref().unwrap_or("(no Class)");
            println!("·     {} {class}", &c.sha[..c.sha.len().min(8)]);
        }
    }
    let clustered: usize = groups.iter().map(Vec::len).sum();
    println!(
        "✓ [commit_class] {} commit(s), {} cluster(s) of {clustered} fix(es)",
        commits.len(),
        groups.len()
    );
    0
}

#[cfg(test)]
#[path = "commit_class_clusters_tests.rs"]
mod tests;
