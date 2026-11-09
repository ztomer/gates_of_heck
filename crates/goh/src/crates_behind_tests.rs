use super::{dry_run, duplicate_majors, parse_moves, unreachable_reason, Move, Outcome};
use std::path::{Path, PathBuf};
use std::time::Duration;

/// `cargo update --dry-run`'s stderr as cargo 1.99 writes it (`gates_of_heck`, 2026-11-09), with a
/// git dependency's move and the lines that are not moves.
const DRY_RUN: &str = "    Updating crates.io index
    Updating git repository `https://github.com/ztomer/gates_of_heck.git`
     Locking 3 packages to highest compatible versions
    Updating crc32fast v1.5.3 -> v1.5.4
    Updating parity v0.1.0 (/tmp/stale) -> v0.2.0
    Updating goh-tui v0.1.0 (https://github.com/ztomer/gates_of_heck.git#1234567) -> #89abcde
      Adding zerocopy v0.8.1
    Removing old v0.1.0
 Downgrading toml v1.2.1+spec-1.1.0 -> v1.1.8+spec-1.1.0
note: pass `--verbose` to see 3 unchanged dependencies behind latest
warning: not updating lockfile due to dry run
";

#[test]
fn a_dry_run_names_each_entry_it_would_move_and_nothing_else() {
    let moves = parse_moves(DRY_RUN);
    let names: Vec<&str> = moves.iter().map(|m| m.name.as_str()).collect();
    assert_eq!(names, ["crc32fast", "goh-tui", "toml"]);
    assert_eq!(
        moves[0],
        Move {
            name: "crc32fast".to_owned(),
            from: "v1.5.3".to_owned(),
            to: "v1.5.4".to_owned()
        }
    );
    let current =
        "    Updating crates.io index\n     Locking 0 packages to highest compatible versions\n";
    assert_eq!(
        parse_moves(current),
        Vec::new(),
        "a current lock moves nothing"
    );
}

#[test]
fn an_unreachable_registry_is_told_from_any_other_failure() {
    let offline =
        "error: failed to get `itoa` as a dependency of package `x v0.1.0`\n\nCaused by:\n  \
                   download of config.json failed\n\nCaused by:\n  [6] Couldn't resolve host name";
    let why = unreachable_reason(offline).expect("a dns failure is the network");
    assert!(why.contains("config.json"), "{why}");
    assert_eq!(
        unreachable_reason("error: failed to parse manifest at `/x/Cargo.toml`"),
        None,
        "a broken manifest is not the network"
    );
}

/// `cargo tree -d --depth 0 --prefix none --format {p}` on `gates_of_heck` and `monitor`, 2026-11-09.
#[test]
fn only_two_compatibility_classes_of_one_crate_are_a_duplicate_major() {
    let tree = "fastrand v2.5.0\n\nfastrand v2.5.0\n\nminiz_oxide v0.8.9\n\nminiz_oxide v0.9.1\n\n\
                smallvec v1.16.4\nsmallvec v1.16.5\ngetrandom v0.4.3 (*)\ngetrandom v0.2.17\n\
                syn v2.0.119\nsyn v3.0.9 (proc-macro)\n";
    let d = duplicate_majors(tree);
    let names: Vec<&str> = d.iter().map(|(n, _)| n.as_str()).collect();
    assert_eq!(names, ["getrandom", "miniz_oxide", "syn"]);
    assert_eq!(d[0].1, ["0.2.17", "0.4.3"], "versions ascend");
}

/// A cargo stand-in that prints `stderr` and exits `code`, or sleeps past any bound.
fn fake_cargo(dir: &Path, name: &str, body: &str) -> PathBuf {
    use std::os::unix::fs::PermissionsExt;
    let p = dir.join(name);
    std::fs::write(&p, format!("#!/bin/sh\n{body}\n")).expect("write fake cargo");
    std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    p
}

fn scratch(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("goh-crates-behind-{tag}-{}", std::process::id()));
    std::fs::create_dir_all(&d).expect("mkdir");
    d
}

#[test]
fn every_way_a_dry_run_ends_has_its_own_outcome() {
    let d = scratch("outcomes");
    let ok = fake_cargo(
        &d,
        "ok",
        "printf '    Updating itoa v1.0.18 -> v1.0.19\\n' >&2",
    );
    let net = fake_cargo(
        &d,
        "net",
        "printf 'error: failed to download from `https://index.crates.io/config.json`\\n' >&2; exit 101",
    );
    let bad = fake_cargo(
        &d,
        "bad",
        "printf 'error: failed to parse manifest\\n' >&2; exit 101",
    );
    let slow = fake_cargo(&d, "slow", "exec sleep 30");
    let t = Duration::from_secs(5);
    let run = |c: &Path, t| dry_run(&c.to_string_lossy(), &d, t);
    assert!(matches!(run(&ok, t), Outcome::Judged(m) if m.len() == 1));
    assert!(matches!(run(&net, t), Outcome::Skipped(w) if w.contains("unreachable")));
    assert!(matches!(run(&bad, t), Outcome::Failed(w) if w.contains("parse manifest")));
    assert!(
        matches!(run(&slow, Duration::from_secs(1)), Outcome::Skipped(w) if w.contains("timed out after 1 s")),
        "a hung cargo is bounded and said, never waited on"
    );
    std::fs::remove_dir_all(&d).expect("rm");
}
