//! `--clusters` calibrated on `ZoneWM`'s `214f0cf7..` and the six clusters its author labelled.

use super::*;

/// `tests/fixtures/commit_class/zonewm_range.tsv`: sha, subject, Class, touched files.
fn zonewm() -> Vec<Commit> {
    include_str!("../../../tests/fixtures/commit_class/zonewm_range.tsv")
        .lines()
        .filter(|l| !l.starts_with('#'))
        .map(|l| {
            let mut f = l.split('\t');
            let mut next = || f.next().unwrap_or("").to_owned();
            let (sha, subject, class, files) = (next(), next(), next(), next());
            Commit {
                sha,
                subject,
                class: Some(class).filter(|c| !c.is_empty()),
                files: files
                    .split(' ')
                    .filter(|p| !p.is_empty())
                    .map(str::to_owned)
                    .collect(),
            }
        })
        .collect()
}

/// The groups as sha sets.
fn groups(file_link: bool) -> Vec<BTreeSet<String>> {
    let mut commits = zonewm();
    if !file_link {
        for c in &mut commits {
            c.files.clear();
        }
    }
    let classes: Vec<String> = commits.iter().filter_map(|c| c.class.clone()).collect();
    clusters(
        &commits,
        &crate::commit_class::similarity::frequent(&classes),
    )
    .into_iter()
    .map(|g| g.into_iter().map(|i| commits[i].sha.clone()).collect())
    .collect()
}

fn together(groups: &[BTreeSet<String>], shas: &[&str]) -> bool {
    groups.iter().any(|g| shas.iter().all(|s| g.contains(*s)))
}

/// The author's clusters a measure of words and files can see: 2 and 6 whole, 4's one pair, and
/// two members each of 1 and 3 (their third shares neither a word nor a file).
const SEEN: [&[&str]; 5] = [
    &["efeacdd1", "95b9cf3c", "f1ef8eb1"],
    &["21194afe", "e3da74b0"],
    &["9e9a6cb7", "5d59c32d"],
    &["3f8e00e1", "5aaf0b95"],
    &["143c3d52", "a776fe2f"],
];

#[test]
fn the_labelled_clusters_words_and_files_can_see() {
    let g = groups(true);
    for cluster in SEEN {
        assert!(together(&g, cluster), "{cluster:?} in {g:?}");
    }
    let exact = |shas: &[&str]| {
        g.iter()
            .any(|c| c.len() == shas.len() && together(std::slice::from_ref(c), shas))
    };
    assert!(
        exact(SEEN[0]) && exact(SEEN[1]),
        "clusters 2 and 6 alone: {g:?}"
    );
}

/// Red-first: today's rule (words only) names 2 of the 6; the file link is what finds the
/// teardown pair and the pictures, whose classes share no content word.
#[test]
fn words_alone_miss_what_the_files_find() {
    let g = groups(false);
    assert!(together(&g, SEEN[0]));
    assert!(!together(&g, SEEN[1]) && !together(&g, SEEN[2]), "{g:?}");
}

/// The author's near-misses stay apart, and the 47-file citation sweep links nothing: through
/// it, 7 unrelated classes were one group.
#[test]
fn near_misses_and_a_sweep_link_nothing() {
    let g = groups(true);
    assert!(!together(&g, &["17e816f3", "ceeeb384"]), "{g:?}");
    assert!(!together(&g, &["5e624bb6", "6a0665e1"]), "{g:?}");
    assert!(!g.iter().any(|c| c.contains("170694f9")), "{g:?}");
    assert!(g.iter().all(|c| c.len() <= 10), "{g:?}");
}
