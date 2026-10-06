"""SCOPE, STAGING, and the refusals — what this gate reads and what it refuses to be quiet about.

Split from `test_check_no_unreaped_spawn.py` at the 500-line cap. These are the tests about the
gate's BOUNDARIES rather than its rules: which files are in scope, that `--staged` reads the index,
that `--exclude` is honoured, and — the one `checks/check_empty_scope.py` exists to enforce in every
gate — that a repo whose test roots were renamed produces a NAMED non-run instead of a pass over
nothing.
"""

from pathlib import Path

import pytest
from unreaped_kit import findings, unreaped_tier  # noqa: F401  # unreaped_tier: a fixture

from conftest import commit_all, git, write

pytestmark = pytest.mark.usefixtures("unreaped_tier")


# ── scope, staging, and the refusals ─────────────────────────────────────────


def test_src_is_not_scoped(repo: Path) -> None:
    """A daemon started by the program is the program's job. Only TESTS are policed, so the gate
    does not march into `src/` and demand that a server never be a server."""
    write(repo, "src/lib.rs", 'fn serve() { let _ = std::process::Command::new("x").spawn(); }\n')
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0, f"{got.stdout}\n{got.stderr}"
    assert "not applicable" in got.stdout + got.stderr, got.stdout


def test_a_rust_unit_test_module_inside_src_is_scoped(repo: Path) -> None:
    """The other half of the scope question: a `#[cfg(test)] mod tests` in `src/` IS a test, and a
    content-based scope is the only thing that can see it."""
    write(
        repo,
        "src/lib.rs",
        "#[cfg(test)]\nmod tests {\n    #[test]\n    fn t() {\n"
        '        let _c = std::process::Command::new("x").spawn().unwrap();\n    }\n}\n',
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 1, f"{got.stdout}\n{got.stderr}"


def test_a_repo_with_no_tests_is_reported_not_passed(repo: Path) -> None:
    """`check_empty_scope.py` holds every gate to this: a pass over nothing is not a pass. The
    phrase is the one that sweep recognises as a named non-run."""
    write(repo, "src/lib.rs", "pub fn add(a: u32, b: u32) -> u32 {\n    a + b\n}\n")
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0
    assert "not applicable" in got.stdout + got.stderr, got.stdout


def test_staged_reads_the_index(repo: Path) -> None:
    """A staged check measures THE INDEX (docs/contracts.md), or it is a check on the wrong object.

    A file that is COMMITTED clean and then EDITED dirty on disk is the discriminating case: a
    worktree-reading checker fails an innocent commit, and an index-reading one refuses to be lied
    to. So the edit stays unstaged here and the verdict must still be clean; then the violation is
    staged and it must be red.
    """
    clean = "import subprocess\n\n\ndef test_x():\n    assert True\n"
    dirty = "import subprocess\n\n\ndef test_x():\n    p = subprocess.Popen(['x'])\n"
    write(repo, "tests/test_a.py", clean)
    commit_all(repo)
    assert findings(repo, "--staged").returncode == 0, "nothing staged, nothing to judge"
    # Staged CLEAN, dirty in the worktree. An index-reading checker is green on this, and that is
    # the half of the contract only a test carrying a worktree edit can show.
    write(repo, "tests/test_a.py", dirty)
    assert findings(repo, "--staged").returncode == 0, "--staged read the WORKTREE, not the index"
    assert findings(repo).returncode == 1, (
        "the worktree edit did not land, so that half proves nothing"
    )
    # ...and the violation, staged.
    git(repo, "add", "tests/test_a.py")
    got = findings(repo, "--staged")
    assert got.returncode == 1, f"{got.stdout}\n{got.stderr}"
    assert "tests/test_a.py:" in got.stdout + got.stderr


def test_exclude_is_honoured(repo: Path) -> None:
    write(
        repo,
        "vendor/tests/test_a.py",
        "import subprocess\n\n\ndef test_x():\n    p = subprocess.Popen(['x'])\n",
    )
    commit_all(repo)
    assert findings(repo, "--exclude", "^vendor/").returncode == 0
    assert findings(repo).returncode == 1


def test_probe_runs_green(repo: Path) -> None:
    got = findings(repo, "--probe")
    assert got.returncode == 0, f"{got.stdout}\n{got.stderr}"


# ── the crate-scope memo (cost fix, 2026-10-05) ──────────────────────────────
#
# `rust_findings` derived `guard_types(crate)`, `drop_types(crate)` and `_known_types(crate)` ONCE
# PER FILE over a crate context that is the same string for the whole scan. Measured over
# `media_server`'s `crates/`: a per-file call cost 134x one without the context, and a pass over
# that 626-file tree took 23.2 s where it now takes 2.1 s. That is a memo, and a memo is exactly
# the shape that can report success over nothing -- so these are the tests that hold it to the same
# bar the gate itself has to meet.


def test_the_memo_serves_a_repeated_question(repo: Path) -> None:
    """The half that makes it a memo: the second identical question does not re-derive.

    Counted rather than timed. A timing assertion would pass on a machine where the derivation is
    cheap and would fail on one where it is not, and neither says anything about whether the answer
    came from the memo.
    """
    import _spawn_rust as sr

    sr.clear_memo()
    crate = "struct Reap(Child);\nimpl Drop for Reap { fn drop(&mut self) { let _ = self.0.wait(); } }\n"
    first = sr.guard_types(crate)
    cached = sr._MEMO.get("guard")
    assert cached is not None and cached[0] == crate, "the answer was not recorded against its text"
    assert sr.guard_types(crate) is first, "an identical question was re-derived instead of served"
    assert sr.crate_facts(crate)[0] == first


def test_the_memo_re_derives_when_the_text_changes() -> None:
    """The half that keeps it from being a stale "clean": a DIFFERENT crate is a miss.

    A single-slot memo keyed on the exact text, so the next crate cannot read the previous one's
    answer. This is the failure `check_empty_scope.py` exists for in a different costume, and the
    reason it is written against a real `Drop` that waits rather than against a counter alone.
    """
    import _spawn_rust as sr

    sr.clear_memo()
    reaps = "struct Reap(Child);\nimpl Drop for Reap { fn drop(&mut self) { let _ = self.0.wait(); } }\n"
    inert = "struct Lame(Child);\nimpl Drop for Lame { fn drop(&mut self) { } }\n"
    assert sr.guard_types(reaps) == {"Reap"}
    assert sr.guard_types(inert) == set(), "a crate with no reaping Drop read as the previous one"
    assert sr.guard_types(reaps) == {"Reap"}, "and going back did not re-derive"
    assert sr.crate_facts(reaps)[0] == {"Reap"}
    assert sr.crate_facts(inert)[0] == set()


def test_the_memo_changes_no_verdict(repo: Path) -> None:
    """The load-bearing one: memoised and not, over a REAL corpus, the findings are the same list.

    A cost fix that moved a rule would be two changes in one commit. This compares the gate run with
    the memo against the gate run with every derivation forced fresh, over a fixture carrying both a
    crate-scope guard and a local empty `Drop` -- the two shapes the crate context exists to tell
    apart.
    """
    write(
        repo,
        "crates/x-rs/tests/common/mod.rs",
        "use std::process::Child;\n\npub struct ReapOnDrop(pub Child);\n\n"
        "impl Drop for ReapOnDrop {\n    fn drop(&mut self) {\n"
        "        let _ = self.0.kill();\n        let _ = self.0.wait();\n    }\n}\n",
    )
    write(
        repo,
        "crates/x-rs/tests/test_guarded.rs",
        "mod common;\n\nuse std::process::Command;\n\nuse common::ReapOnDrop;\n\n#[test]\n"
        'fn t() {\n    let mut child = ReapOnDrop(Command::new("x").spawn().expect("runs"));\n'
        '    assert!(child.0.id() > 0, "panics; the guard reaps on unwind");\n}\n',
    )
    write(
        repo,
        "crates/x-rs/tests/test_shadowed.rs",
        "use std::process::{Child, Command};\n\nstruct ReapOnDrop(Child);\n\n"
        "impl Drop for ReapOnDrop {\n    fn drop(&mut self) {\n    }\n}\n\n"
        "impl ReapOnDrop {\n    fn spawn(c: &mut Command) -> Self {\n        Self(c.spawn().unwrap())\n    }\n}\n\n"
        '#[test]\nfn t() {\n    let _g = ReapOnDrop::spawn(Command::new("sh"));\n'
        '    panic!("the failing write_all lands here");\n}\n',
    )
    commit_all(repo)
    memoised = findings(repo)
    unmemoised = findings(repo, "--fresh-derivations")
    assert memoised.returncode == unmemoised.returncode == 1, (
        f"{memoised.stdout}\n{memoised.stderr}\n---\n{unmemoised.stdout}\n{unmemoised.stderr}"
    )
    findings_of = lambda got: [  # noqa: E731
        line for line in (got.stdout + got.stderr).splitlines() if ".rs:" in line
    ]
    assert findings_of(memoised) == findings_of(unmemoised)
    assert any("test_shadowed.rs" in line for line in findings_of(memoised)), findings_of(memoised)
    assert not any("test_guarded.rs" in line for line in findings_of(memoised)), findings_of(
        memoised
    )
