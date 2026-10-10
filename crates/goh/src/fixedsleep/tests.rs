//! The finder over the three shapes `ZoneWM` shipped, the shapes that must stay clean, and the
//! seed's verdict in every direction.

use super::*;

fn found(source: &str) -> Vec<(usize, String)> {
    scan::findings(source)
        .unwrap_or_else(|e| panic!("{e}"))
        .into_iter()
        .map(|f| (f.line, f.read))
        .collect()
}

#[test]
fn a_lock_read_as_held_after_a_fixed_wait_is_found() {
    let src =
        "import time\nproc = start()\ntime.sleep(1)\nif proc.poll() is None:\n    held = True\n";
    assert_eq!(found(src), [(3, "proc.poll".to_owned())]);
}

#[test]
fn a_marker_read_after_a_fixed_wait_is_found() {
    let src = "import time\ndef f(marker):\n    time.sleep(0.5)\n    return marker.read_text()\n";
    assert_eq!(found(src), [(3, "marker.read_text".to_owned())]);
}

#[test]
fn a_returncode_read_after_an_awaited_sleep_is_found() {
    let src =
        "import asyncio\nasync def f(p):\n    await asyncio.sleep(2)\n    rc = p.returncode\n";
    assert_eq!(found(src), [(3, "p.returncode".to_owned())]);
}

#[test]
fn a_sleep_inside_a_poll_loop_is_not_a_stand_in() {
    let src = "import time\nwhile time.monotonic() < deadline:\n    if p.poll() is not None:\n        break\n    time.sleep(0.1)\n    if p.poll() is not None:\n        break\n";
    assert_eq!(found(src), []);
}

#[test]
fn a_sleep_in_an_if_inside_a_loop_is_still_in_the_loop() {
    let src = "import time\nfor _ in range(9):\n    if slow:\n        time.sleep(1)\n        ok = path.exists()\n";
    assert_eq!(found(src), []);
}

#[test]
fn a_def_inside_a_loop_is_its_own_scope() {
    let src = "import time\nfor _ in range(2):\n    def f(p):\n        time.sleep(1)\n        return p.poll()\n";
    assert_eq!(found(src), [(4, "p.poll".to_owned())]);
}

#[test]
fn a_sleep_before_a_while_that_polls_is_a_warm_up() {
    let src = "import time\ntime.sleep(1)\nwhile p.poll() is None:\n    time.sleep(0.1)\n";
    assert_eq!(found(src), []);
}

#[test]
fn a_sleep_followed_by_unrelated_work_is_not_a_finding() {
    let src = "import time\ntime.sleep(1)\nx = compute()\n";
    assert_eq!(found(src), []);
}

fn scanned(entries: &[(&str, usize)]) -> Scanned {
    let mut s = Scanned {
        read: 3,
        ..Scanned::default()
    };
    for &(path, n) in entries {
        let sites = (1..=n)
            .map(|line| scan::Finding {
                line,
                read: "p.poll".to_owned(),
            })
            .collect();
        s.found.insert(path.to_owned(), sites);
    }
    s
}

fn seed(entries: &[(&str, usize)]) -> BTreeMap<String, usize> {
    entries.iter().map(|&(p, n)| (p.to_owned(), n)).collect()
}

#[test]
fn a_seeded_file_at_its_count_passes_and_says_what_it_read() {
    let (code, text) = judge(&scanned(&[("a.py", 2)]), &seed(&[("a.py", 2)]));
    assert_eq!(code, 0, "{text}");
    assert!(text.contains("3 Python file(s) read, 2 seeded"), "{text}");
}

#[test]
fn a_new_site_is_red_and_named() {
    let (code, text) = judge(&scanned(&[("a.py", 3)]), &seed(&[("a.py", 2)]));
    assert_eq!(code, 1);
    assert!(text.contains("a.py:3:"), "{text}");
}

#[test]
fn an_unseeded_file_with_a_site_is_red() {
    let (code, _) = judge(&scanned(&[("b.py", 1)]), &seed(&[]));
    assert_eq!(code, 1);
}

#[test]
fn a_fixed_site_must_lower_the_seed() {
    let (code, text) = judge(&scanned(&[("a.py", 1)]), &seed(&[("a.py", 2)]));
    assert_eq!(code, 1);
    assert!(text.contains("lower the seed"), "{text}");
}

#[test]
fn an_entry_for_a_file_gone_clean_is_stale() {
    let (code, text) = judge(&scanned(&[]), &seed(&[("gone.py", 1)]));
    assert_eq!(code, 1);
    assert!(
        text.contains("gone.py is seeded with 1 and has 0"),
        "{text}"
    );
}

#[test]
fn an_unparseable_file_is_named_never_passed() {
    let mut s = scanned(&[]);
    s.unreadable
        .push(("py2.py".to_owned(), "bad syntax".to_owned()));
    let (code, text) = judge(&s, &seed(&[]));
    assert_eq!(code, 1);
    assert!(text.contains("py2.py"), "{text}");
}

#[test]
fn the_seed_round_trips_and_a_corrupt_one_is_an_error() {
    let dir = tempfile::tempdir().unwrap_or_else(|e| panic!("{e}"));
    let s = scanned(&[("a.py", 2), ("b.py", 1)]);
    std::fs::write(dir.path().join(SEED_FILE), seed_text(&s)).unwrap_or_else(|e| panic!("{e}"));
    assert_eq!(load_seed(dir.path()), Ok(seed(&[("a.py", 2), ("b.py", 1)])));
    std::fs::write(dir.path().join(SEED_FILE), "{\"files\": {\"a.py\": \"x\"}}")
        .unwrap_or_else(|e| panic!("{e}"));
    assert!(load_seed(dir.path()).is_err());
    std::fs::write(dir.path().join(SEED_FILE), "not json").unwrap_or_else(|e| panic!("{e}"));
    assert!(load_seed(dir.path()).is_err());
}
