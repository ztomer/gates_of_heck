"""SCOPE, STAGING, and the refusals — what this gate reads and what it refuses to be quiet about.

Split from `test_check_no_unreaped_spawn.py` at the 500-line cap. These are the tests about the
gate's BOUNDARIES rather than its rules: which files are in scope, that `--staged` reads the index,
that `--exclude` is honoured, and — the one `checks/check_empty_scope.py` exists to enforce in every
gate — that a repo whose test roots were renamed produces a NAMED non-run instead of a pass over
nothing.
"""

from pathlib import Path

from unreaped_kit import findings

from conftest import commit_all, git, write


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
