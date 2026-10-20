"""check_tracked_build_output.py — a tracked file that is build output.

koffee_oss asked for this on 2026-10-19 (BACKLOG.md, "no tracked build output"): six files under
`DerivedDataTests/` came in with the history flatten, and the `.gitignore` check cannot see them --
it cannot see a file that is ALREADY tracked, which is the only case that matters.

The gate reads the TRACKED files, so these tests are about the difference between a file that is
tracked and one that is merely on disk: the second is `.gitignore`'s business, and the house's
tracked-ignored step already covers a file that is tracked AND ignored.
"""

from __future__ import annotations

from conftest import run_check, stage, write

GATE = "checks/check_tracked_build_output.py"


def run(repo, *args):
    return run_check(repo, GATE, *args)


def test_a_clean_tree_is_green(repo):
    write(repo, "src/main.rs", "fn main() {}\n")
    stage(repo, "src/main.rs")
    r = run(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "looks like build output" in r.stdout, r.stdout


def test_a_tracked_build_output_is_red_and_named(repo):
    write(repo, "target/debug/app", "binary\n")
    stage(repo, "target/debug/app")
    r = run(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "target/debug/app" in r.stderr, r.stderr
    assert "build directory" in r.stderr, r.stderr


def test_a_repository_that_tracks_nothing_is_refused_not_passed(tmp_path):
    """A scan that found no subject has judged nothing: the empty sweep's class, refused."""
    from _fast_git import fast_init

    empty = tmp_path / "empty"
    empty.mkdir()
    fast_init(empty, "main")
    r = run_check(empty, GATE)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "tracks no files" in r.stderr, r.stderr


def test_a_build_output_that_is_only_on_disk_is_not_a_finding(repo):
    """Untracked is `.gitignore`'s business, and this gate is about the other case."""
    write(repo, "src/main.rs", "fn main() {}\n")
    stage(repo, "src/main.rs")
    write(repo, "target/debug/app", "binary\n")
    r = run(repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_file_whose_NAME_is_a_shape_word_is_not_a_finding(repo):
    """`target.md` is a document; `target/` is a build directory. The shape is a PATH SEGMENT."""
    write(repo, "docs/target.md", "# target\n")
    stage(repo, "docs/target.md")
    r = run(repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_shape_is_caught(repo):
    """One case per shape: a gate that reads only one of them is a gate that misses the rest."""
    cases = {
        "target/debug/app": "build directory",
        "DerivedData/App/Build/x": "build directory",
        "pkg/__pycache__/m.cpython-311.pyc": "build directory",
        "web/node_modules/left-pad/index.js": "build directory",
        ".build/checkouts/x": "build directory",
        ".venv/lib/python3.11/x.py": "build directory",
        "build/libthing.a": "compiled file",
        "obj/main.o": "compiled file",
        "lib/libthing.so": "compiled file",
        "lib/libthing.dylib": "compiled file",
        "pkg/m.pyc": "compiled file",
        "pkg/m.class": "compiled file",
        "Logs/build.xcactivitylog": "compiled file",
        "build/App.dSYM/Contents/x": "build bundle",
        "dist/App.app/Contents/x": "build bundle",
        "dist/App.ipa": "build bundle",
    }
    for path, why in cases.items():
        write(repo, path, "x\n")
        stage(repo, path)
        r = run(repo)
        assert r.returncode == 1, (path, r.stdout + r.stderr)
        assert why in r.stderr, (path, r.stderr)
        # One case at a time: the next case must not inherit this one's finding.
        (repo / path).unlink()
        stage(repo, path)
