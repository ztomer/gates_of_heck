//! The check's specification as tables, on the estate's own shapes: `sys_updater`'s and ztools'
//! shallow gate jobs (DV-4), divoom-control's full clone (DV.FETCHDEPTH 114bea5), app-mcp-bridge's
//! clone of goh's default branch, and the v0.95.0 pins every workflow held on 2026-11-12.

use super::scan::{findings, is_stale, reaches_reader, version, Problem, WINDOW};

const OWN: (u64, u64, u64) = (0, 121, 0);

fn none(_: &str) -> Option<String> {
    None
}

#[derive(Debug, PartialEq, Eq)]
enum K {
    Unpinned,
    Stale,
    Shallow,
    Unparsable,
}

fn kinds_with(
    src: &str,
    changelog: bool,
    read: &dyn Fn(&str) -> Option<String>,
) -> Vec<(usize, K)> {
    findings(src, OWN, changelog, read)
        .into_iter()
        .map(|f| {
            let k = match f.problem {
                Problem::Unpinned(_) => K::Unpinned,
                Problem::Stale(_) => K::Stale,
                Problem::Shallow(_) => K::Shallow,
                Problem::Unparsable(_) => K::Unparsable,
            };
            (f.line, k)
        })
        .collect()
}

fn kinds(src: &str) -> Vec<(usize, K)> {
    kinds_with(src, true, &none)
}

/// A job whose first step is on line 6.
fn job(steps: &str) -> String {
    format!("on: push\njobs:\n  gate:\n    runs-on: ubuntu-24.04\n    steps:\n{steps}")
}

const OWN_CHECKOUT: &str =
    "      - uses: actions/checkout@v7.0.1\n        with:\n          persist-credentials: false\n";
const FULL_CHECKOUT: &str = "      - uses: actions/checkout@v7.0.1\n        with:\n          fetch-depth: 0\n          persist-credentials: false\n";

fn goh_checkout(r: &str) -> String {
    format!(
        "      - uses: actions/checkout@v7.0.1\n        with:\n          repository: ztomer/gates_of_heck\n{r}          path: .gates_of_heck\n"
    )
}

#[test]
fn versions_and_the_window() {
    assert_eq!(version("v0.121.0"), Some((0, 121, 0)));
    assert_eq!(version("0.121.0"), Some((0, 121, 0)));
    assert_eq!(version("main"), None);
    assert_eq!(version("v1.2"), None);
    assert!(is_stale((0, 95, 0), OWN), "DV-4's v0.95.0 against v0.121.0");
    assert!(is_stale((0, 121 - WINDOW - 1, 9), OWN));
    assert!(
        !is_stale((0, 121 - WINDOW, 0), OWN),
        "the window's edge passes"
    );
    assert!(!is_stale((0, 121, 0), OWN));
    assert!(
        !is_stale((0, 130, 0), OWN),
        "a pin newer than the running goh: that goh is old"
    );
    assert!(is_stale((0, 200, 0), (1, 0, 0)), "a major behind");
}

#[test]
fn a_goh_checkout_pin_is_judged_on_its_ref_line() {
    let fresh = job(&format!(
        "{FULL_CHECKOUT}{}",
        goh_checkout("          ref: v0.121.0\n")
    ));
    assert_eq!(kinds(&fresh), vec![]);
    let old = job(&format!(
        "{FULL_CHECKOUT}{}",
        goh_checkout("          ref: v0.95.0\n")
    ));
    assert_eq!(kinds(&old), vec![(13, K::Stale)]);
    let branch = job(&format!(
        "{FULL_CHECKOUT}{}",
        goh_checkout("          ref: main\n")
    ));
    assert_eq!(kinds(&branch), vec![(13, K::Unpinned)]);
    let bare = job(&format!("{FULL_CHECKOUT}{}", goh_checkout("")));
    assert_eq!(
        kinds(&bare),
        vec![(10, K::Unpinned)],
        "no ref: goh's default branch"
    );
}

#[test]
fn goh_tag_env_and_clone_pins() {
    let env = |tag: &str| {
        format!(
            "on: push\nenv:\n  GOH_TAG: {tag}\njobs:\n  gate:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: git clone --depth 1 --branch \"$GOH_TAG\" https://github.com/ztomer/gates_of_heck \"$GOH_DIR\"\n"
        )
    };
    assert_eq!(kinds(&env("v0.121.0")), vec![]);
    assert_eq!(kinds(&env("v0.96.0")), vec![(3, K::Stale)]);
    let clone = |arg: &str| {
        job(&format!(
            "      - name: goh\n        run: |\n          set -euo pipefail\n          git clone {arg}https://github.com/ztomer/gates_of_heck \"$GOH_DIR\"\n"
        ))
    };
    assert_eq!(kinds(&clone("--branch v0.121.0 ")), vec![]);
    assert_eq!(kinds(&clone("--branch v0.95.0 ")), vec![(9, K::Stale)]);
    assert_eq!(
        kinds(&clone("")),
        vec![(9, K::Unpinned)],
        "app-mcp-bridge's live clone"
    );
    let commented = job("      - run: |\n          # git clone https://github.com/ztomer/gates_of_heck\n          true\n");
    assert_eq!(kinds(&commented), vec![]);
}

#[test]
fn a_tag_reading_job_on_a_shallow_clone_is_red_on_its_checkout() {
    let direct = "      - run: bash \"$GOH_DIR/gates/structural.sh\" --full\n";
    assert_eq!(
        kinds(&job(&format!("{OWN_CHECKOUT}{direct}"))),
        vec![(6, K::Shallow)]
    );
    assert_eq!(kinds(&job(&format!("{FULL_CHECKOUT}{direct}"))), vec![]);
    let quoted = FULL_CHECKOUT.replace("fetch-depth: 0", "fetch-depth: '0'");
    assert_eq!(kinds(&job(&format!("{quoted}{direct}"))), vec![]);
    let one = FULL_CHECKOUT.replace("fetch-depth: 0", "fetch-depth: 1");
    assert_eq!(
        kinds(&job(&format!("{one}{direct}"))),
        vec![(6, K::Shallow)]
    );
    // No changelog: changelog-tags has nothing to judge, so the clone's depth is the repo's call.
    assert_eq!(
        kinds_with(&job(&format!("{OWN_CHECKOUT}{direct}")), false, &none),
        vec![]
    );
    // A job that runs no gate (cargo test) may stay shallow.
    let tests = "      - run: cargo test --workspace\n";
    assert_eq!(kinds(&job(&format!("{OWN_CHECKOUT}{tests}"))), vec![]);
}

#[test]
fn the_reader_is_found_one_level_down() {
    let gate = |rel: &str| {
        match rel {
        "tools/gate.sh" => Some("exec \"$GOH/gates/local_ci.sh\" .\n".to_owned()),
        "tools/run_proofs.sh" => Some("cargo test\n".to_owned()),
        // monitor's: it names the gate in prose and runs none of it.
        "tools/ratchet_check.py" => Some("\"\"\"Runs from HERE, not only from `structural.sh`.\"\"\"\n".to_owned()),
        "tools/commented.sh" => Some("# exec \"$GOH/gates/local_ci.sh\" .\ncargo test\n".to_owned()),
        "Makefile" => Some(
            "test:\n\tcargo test\n\nci: ## the gate of record\n\t\"$${GOH_DIR}/gates/local_ci.sh\" .\n"
                .to_owned(),
        ),
        _ => None,
    }
    };
    assert!(reaches_reader(
        "env -u RUNNER_TRACKING_ID tools/gate.sh --full",
        &gate
    ));
    assert!(reaches_reader("./tools/gate.sh --full", &gate));
    assert!(
        reaches_reader("make ci", &gate),
        "ztools: make ci -> local_ci.sh"
    );
    assert!(!reaches_reader("make test", &gate));
    assert!(!reaches_reader("bash tools/run_proofs.sh", &gate));
    assert!(
        !reaches_reader("python3 tools/ratchet_check.py", &gate),
        "a prose mention runs nothing"
    );
    assert!(
        !reaches_reader("bash tools/commented.sh", &gate),
        "a commented-out call runs nothing"
    );
    assert!(reaches_reader("\"$GOH_DIR/bin/goh\" changelog-tags", &none));
    let ztools = job(&format!("{OWN_CHECKOUT}      - run: make ci\n"));
    assert_eq!(kinds_with(&ztools, true, &gate), vec![(6, K::Shallow)]);
}

#[test]
fn a_checkout_of_another_repo_is_not_the_jobs_own() {
    let other = job(&format!(
        "{}      - run: bash .gates_of_heck/gates/structural.sh --full\n",
        goh_checkout("          ref: v0.121.0\n")
    ));
    assert_eq!(
        kinds(&other),
        vec![],
        "no checkout of the repo itself: no clone to judge"
    );
}

#[test]
fn unparsable_yaml_is_a_finding() {
    assert_eq!(kinds("jobs: [\n"), vec![(2, K::Unparsable)]);
}

/// The running goh's release is its tag's, and a build past a tag is that tag's release
/// (GOH.SHIPRELREUSE: the version is `lib/goh_version.sh`'s, never `Cargo.toml`'s).
#[test]
fn a_build_past_a_tag_is_that_tags_release() {
    assert_eq!(super::release_of("0.125.0"), (0, 125, 0));
    assert_eq!(super::release_of("0.124.0-3-g0123abcdef"), (0, 124, 0));
    assert_eq!(
        super::release_of("0.124.0-3-g0123abcdef-dirty"),
        (0, 124, 0)
    );
    assert_eq!(super::release_of("unknown"), (0, 0, 0));
}
