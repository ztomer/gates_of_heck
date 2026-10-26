"""SCOPE, STAGING, and the refusals — what this gate reads and what it refuses to be quiet about.

Split from `test_check_no_unreaped_spawn.py` at the 500-line cap. These are the tests about the
gate's BOUNDARIES rather than its rules: which files are in scope, that `--staged` reads the index,
that `--exclude` is honoured, and — the one `checks/check_empty_scope.py` exists to enforce in every
gate — that a repo whose test roots were renamed produces a NAMED non-run instead of a pass over
nothing.
"""

from pathlib import Path

import pytest
from conftest import commit_all, git, write
from unreaped_kit import findings, unreaped_tier  # noqa: F401  # unreaped_tier: a fixture

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


# ── the crate's SCOPE: a guard is a CROSS-FILE contract ────────────────────────

# The shape `routines` has: `tests/lifecycle_support/mod.rs` defines the guard ONCE,
# and the test files that use it live beside it (commit b961bb0, 2026-10-20). The
# estate test in `test_no_unreaped_spawn_estate.py` pins this shape at FULL scope; the
# two below are its STAGED twin, and the scope they pin is the one the step read wrongly.
SHARED_GUARD = """\
use std::process::Child;

/// A child that is stopped AND collected even when the test PANICS.
pub struct ReapOnDrop(pub Child);

impl Drop for ReapOnDrop {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
"""

# The same guard, emptied: the type still CLAIMS to clean up on unwind, and does not.
BROKEN_GUARD = """\
use std::process::Child;

/// A child that is stopped AND collected even when the test PANICS.
pub struct ReapOnDrop(pub Child);

impl Drop for ReapOnDrop {
    fn drop(&mut self) {
        let _ = &self.0;
    }
}
"""

# The file that RELIES on the guard: correct code, in every respect.
GUARD_USER = """\
mod common;

use std::process::Command;

use common::ReapOnDrop;

#[test]
fn t() {
    let mut child = ReapOnDrop(Command::new("x").spawn().expect("runs"));
    assert!(child.0.id() > 0, "panics; the guard reaps on unwind");
}
"""


def test_a_shared_guard_is_protection_to_the_file_the_commit_brings(repo: Path) -> None:
    """A guard defined ONCE in `tests/common/` protects every file that uses it, and the step must
    SEE it when the commit brings the user and not the guard.

    The false positive this pins: the step read the guard types from the files the commit brings,
    so the shared guard was invisible and the wrap looked like a bare `Child` -- a red on correct
    code, in the exact files the guard was written for. `routines` hit it on 2026-10-20 and
    worked around it by giving three of its test files a guard of their own (b961bb0), which is
    the workaround, not the fix: the guard the step cannot see is still the guard the tree has.
    """
    write(repo, "tests/common/mod.rs", SHARED_GUARD)
    write(repo, "tests/test_uses.rs", GUARD_USER)
    commit_all(repo)
    # The commit brings the USER. The guard's own file is untouched by it.
    write(repo, "tests/test_uses.rs", GUARD_USER + "\n// the commit touches this file\n")
    git(repo, "add", "tests/test_uses.rs")
    got = findings(repo, "--staged")
    out = got.stdout + got.stderr
    assert got.returncode == 0, f"a shared guard was invisible to the file that uses it:\n{out}"


def test_a_commit_that_breaks_a_shared_guard_is_red(repo: Path) -> None:
    """The other direction, and the one that decides the scope: a commit that touches NOTHING but
    the guard's own file must still be refused when it breaks the guard.

    The step read the files the commit brings, so the files that RELY on the guard were not in
    scope -- and the tree leaked while the gate passed it. That is the shape a gate must not
    have: it measures the COMMIT, and the commit is not where the leak is.
    """
    write(repo, "tests/common/mod.rs", SHARED_GUARD)
    write(repo, "tests/test_uses.rs", GUARD_USER)
    commit_all(repo)
    # The commit breaks the guard and changes nothing else.
    write(repo, "tests/common/mod.rs", BROKEN_GUARD)
    git(repo, "add", "tests/common/mod.rs")
    got = findings(repo, "--staged")
    out = got.stdout + got.stderr
    assert got.returncode == 1, f"a broken shared guard was accepted:\n{out}"
    assert "tests/test_uses.rs:" in out, f"the leak was not named where it happens:\n{out}"


def test_the_staged_scope_reads_the_index_and_not_the_worktree(repo: Path) -> None:
    """The widened scope must still read the INDEX: an unstaged edit is not part of the commit, so
    it must not be judged -- and must not block a clean commit either.

    The discrimination is the worktree-only reading: it judges the unstaged edit (red here) and
    it misses the staged one (green). Both halves are the same edit, so only the index answers
    both questions.
    """
    clean = "import subprocess\n\n\ndef test_x():\n    assert True\n"
    dirty = 'import subprocess\n\n\ndef test_x():\n    p = subprocess.Popen(["x"])\n'
    write(repo, "tests/test_a.py", clean)
    commit_all(repo)
    write(repo, "tests/test_a.py", dirty)
    got = findings(repo, "--staged")
    assert got.returncode == 0, f"an unstaged edit was judged at --staged:\n{got.stdout}"
    assert findings(repo).returncode == 1, "the worktree edit did not land, so that proves nothing"
    # ...and the staged version of the same file IS judged.
    git(repo, "add", "tests/test_a.py")
    got = findings(repo, "--staged")
    assert got.returncode == 1, f"the staged violation passed:\n{got.stdout}\n{got.stderr}"
    assert "tests/test_a.py:" in got.stdout + got.stderr


# The retired Python checker's `--probe` and its crate-scope memo tests (`_spawn_rust`'s
# single-slot memo, and `--fresh-derivations` to prove it changed no verdict) went with it in
# Phase N3. The native port derives a crate's facts once per crate by construction.
