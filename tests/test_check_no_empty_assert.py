"""check_no_empty_assert.py — the measured clippy table, and the gate's own
scope rules.

The cases are not invented here: each one is a line of the table in the
checker's docstring, which was measured against clippy 1.99.0 on 2026-10-02,
one shape per line, with the lint reported by clippy itself. The `clean` rows
carry as much weight as the dirty ones — a checker that flagged
`assert_eq!(v.len(), 0)` would be flagging the fix clippy asks for.
"""

import subprocess
import sys

from conftest import REPO_ROOT, commit_all, run_check, stage, write

SCRIPT = "checks/check_no_empty_assert.py"

# (source, is_a_finding) — see the module docstring's measured table.
DIRTY = [
    "assert!(v.is_empty());",
    "assert!(take!(&d).is_empty());",
    # A receiver with a string literal in it. The receiver pattern was once a
    # character class without `"`, so every one of these passed a gate whose
    # whole job is catching this shape.
    'assert!(Style::load_zstyle(&dir.join("missing")).is_empty());',
    'assert!(load("a").is_empty(), "no rows");',
    "assert!(v.ok()?.is_empty());",
    "assert!(!v.is_empty());",
    "assert!(v.len() == 0);",
    "assert!(0 == v.len());",
    "assert!(v.len() > 0);",
    "debug_assert!(v.is_empty());",
    # The two rows clippy does NOT report, and the reason this checker exists.
    'assert!(v.is_empty(), "with a message");',
    'assert!(!v.is_empty(), "msg");',
    "assert!(s\n    .as_bytes()\n    .is_empty());",
    'assert!(s.as_bytes().is_empty(),\n    "split message");',
]

CLEAN = [
    # clippy's own suggestions, and the honest way to say "not empty".
    "assert_eq!(v.len(), 0);",
    "assert_ne!(v.len(), 0);",
    "assert_eq!(v.to_vec(), Vec::<u8>::new());",
    "assert_eq!(v.len(), 0usize);",
    # The COMPOUND and NESTED forms, which clippy reports none of. They are here
    # because an earlier version of the gate flagged all of them, and one of them
    # has no spelling both lints accept: `is_some_and(|x| !x.is_empty())` is what
    # clippy asks for, and `x.len() > 0` is what this gate used to ask for, so a
    # correctly written routine failed one gate or the other.
    "assert!(v.is_empty() && ok);",
    "assert!(ok && v.is_empty());",
    "assert!(!v.is_empty() || ok);",
    "assert!(v.is_empty() == true);",
    "assert!(v.first().is_some_and(|x| !x.is_empty()));",
    "assert!(v.is_empty() && w.is_empty());",
    "assert!(matches!(v.len(), 0));",
    # Not the shape: a bound variable, a different emptiness test, another macro.
    "let x = v.is_empty(); assert!(x);",
    "assert!(v.iter().next().is_none());",
    "my_assert!(v.is_empty());",
    "a::assert!(v.is_empty());",
    # Prose and fixtures are not code.
    "// assert!(v.is_empty()); is what we do not write\nfn f() {}\n",
    "/* assert!(v.is_empty()); */\nfn f() {}\n",
    'const S: &str = "assert!(v.is_empty());";\nfn f() {}\n',
]


def _mkcrate(repo):
    (repo / "src").mkdir(exist_ok=True)
    return repo / "src"


def test_every_measured_dirty_shape_fails(repo):
    for i, source in enumerate(DIRTY):
        src = _mkcrate(repo) / "lib.rs"
        src.write_text(f"fn f() {{\n    {source}\n}}\n", encoding="utf-8")
        commit_all(repo)
        r = run_check(repo, SCRIPT)
        assert r.returncode == 1, f"shape {i} not caught: {source!r}\n{r.stdout}"
        assert "no_empty_assert" in r.stdout


def test_every_measured_clean_shape_passes(repo):
    """The half that keeps the gate from being a nuisance: flagging the fix is
    how a shape gate gets switched off, and it is silent while it happens."""
    src = _mkcrate(repo) / "lib.rs"
    body = "\n".join(f"    {line}" if line else line for line in CLEAN)
    src.write_text(f"fn f() {{\n{body}\n}}\n", encoding="utf-8")
    commit_all(repo)
    r = run_check(repo, SCRIPT)
    assert r.returncode == 0, r.stdout


def test_a_multiline_assert_is_reported_at_its_opening_line(repo):
    src = _mkcrate(repo) / "lib.rs"
    src.write_text(
        "fn f() {\n    assert!(s\n        .as_bytes()\n        .is_empty());\n}\n",
        encoding="utf-8",
    )
    commit_all(repo)
    r = run_check(repo, SCRIPT)
    assert r.returncode == 1
    # Line 2 is the `assert!(`, not line 4 where the call closes.
    assert "lib.rs:2:" in r.stdout, r.stdout


def test_a_paren_in_a_message_does_not_end_the_scan(repo):
    """`")"` inside a message is quoted text. Counting it would close the
    invocation early and report a finding for a macro that is perfectly fine."""
    src = _mkcrate(repo) / "lib.rs"
    src.write_text(
        'fn f() {\n    assert_eq!(format!("{}", 1), "1", "a ) in a message");\n'
        "    assert_eq!(v.len(), 0);\n}\n",
        encoding="utf-8",
    )
    commit_all(repo)
    assert run_check(repo, SCRIPT).returncode == 0


def test_staged_mode_reads_the_index_not_the_worktree(repo):
    """The pre-commit question is "is the COMMIT clean", and it has to be able to
    say yes. A dirty worktree with a clean index is the normal state of a
    developer mid-edit, so `--staged` must be green there while the same tree is
    red unstaged — which is what makes it a pre-commit filter rather than a
    second copy of the CI gate."""
    src = _mkcrate(repo) / "lib.rs"
    src.write_text("fn f() {}\n", encoding="utf-8")
    commit_all(repo)
    src.write_text("fn f() {\n    assert!(v.is_empty());\n}\n", encoding="utf-8")

    unstaged = run_check(repo, SCRIPT)
    assert unstaged.returncode == 1, "the worktree IS dirty; a full run must see it"

    precommit = run_check(repo, SCRIPT, "--staged")
    assert precommit.returncode == 0, (
        "an unstaged edit is not this commit's business:\n" + precommit.stdout
    )

    stage(repo, "src/lib.rs")
    r = run_check(repo, SCRIPT, "--staged")
    assert r.returncode == 1, r.stdout
    assert "staged" in r.stdout


def test_generated_files_are_exempt(repo):
    """`@generated` in the first 40 lines, the same marker and window
    check_no_allow.py uses — prost and friends emit this shape by the hundred."""
    src = _mkcrate(repo) / "lib.rs"
    src.write_text(
        "// @generated by prost-build, do not edit\nfn f() {\n    assert!(v.is_empty());\n}\n",
        encoding="utf-8",
    )
    commit_all(repo)
    assert run_check(repo, SCRIPT).returncode == 0


def test_a_mention_of_the_marker_is_not_the_marker(repo):
    """A doc comment that NAMES the marker does not exempt the file. Counting
    mentions exempted a source file from itself (2026-09-23)."""
    src = _mkcrate(repo) / "lib.rs"
    src.write_text(
        "/// exempt when a generator writes `@generated` in the first 40 lines\n"
        "fn f() {\n    assert!(v.is_empty());\n}\n",
        encoding="utf-8",
    )
    commit_all(repo)
    assert run_check(repo, SCRIPT).returncode == 1


def test_exclude_skips_a_vendored_tree(repo):
    # Under a src/ dir, so the only thing keeping it out of the report is the
    # exclusion — a vendored file in a place the scope rule already skips would
    # pass whether or not --exclude works.
    (repo / "vendor" / "thing" / "src").mkdir(parents=True)
    write(repo, "vendor/thing/src/lib.rs", "fn f() {\n    assert!(v.is_empty());\n}\n")
    src = _mkcrate(repo) / "lib.rs"
    src.write_text("fn f() {}\n", encoding="utf-8")
    commit_all(repo)
    assert run_check(repo, SCRIPT).returncode == 1
    assert run_check(repo, SCRIPT, "--exclude", "^vendor/").returncode == 0


def test_a_rust_repo_with_no_compiled_source_is_not_a_clean_run(repo):
    """The empty-scope blindness this repo has been bitten by twice. A manifest
    with no source under src/ means the layout moved, and "0 files clean" reads
    exactly like a spotless crate."""
    write(repo, "Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    commit_all(repo)
    r = run_check(repo, SCRIPT)
    assert r.returncode == 1
    assert "NO compiled source" in r.stderr


def test_a_repo_with_no_rust_is_not_applicable(repo):
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    commit_all(repo)
    r = run_check(repo, SCRIPT)
    assert r.returncode == 0, r.stdout


def test_the_probe_runs_and_is_green():
    """check_probes_pass.py discovers this by source, then RUNS it."""
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / SCRIPT), "--probe"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "measured shapes agree" in r.stdout


def test_calibration_the_probe_goes_red_when_a_shape_is_dropped(tmp_path):
    """The load-bearing half, seen by the gate that RUNS the probe.

    A probe that cannot fail is a document. This copies the checker, replaces one
    of its patterns with one that can never match, and asserts the probe reports
    the rows that relied on it -- so the calibration is regenerated by breaking the
    code, not by asserting it.

    Anchored to the pattern's NAME, not its text: an earlier version broke the
    regex source, and every later fix to that regex silently disarmed this test
    (the `assert broken != src` fired, which is the failure mode this comment is
    about).
    """
    import os
    import re as _re

    src = (REPO_ROOT / SCRIPT).read_text(encoding="utf-8")
    broken = _re.sub(
        r"^_CONDITION_IS_EMPTY = re\.compile\(.*$",
        '_CONDITION_IS_EMPTY = re.compile(r"^$")',
        src,
        count=1,
        flags=_re.MULTILINE,
    )
    assert broken != src, "the pattern to break has moved; update this test"
    target = tmp_path / "broken.py"
    target.write_text(broken, encoding="utf-8")
    # The copy sits outside checks/, so the sibling imports are put back on the
    # path explicitly rather than by living next to the module it came from.
    r = subprocess.run(
        [sys.executable, str(target), "--probe"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "checks")},
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert _re.search(r"wanted a finding=True, got False", r.stdout + r.stderr)
