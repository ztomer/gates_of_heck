"""`goh proven` is gates/_proven.sh's hot path in-process, and the two are interchangeable (4C.2).

A key was `git status` + `write-tree` + a `shasum` (Perl) + `cut`, and a lookup one more git call,
per step per group per gate. The native half computes the same bytes: these tests pin that the bash
and the native agree on the tree key and the scoped key over the same repo and identity, and that a
record written by either is found by the other -- so a pre-commit hook on one side and a push on
the other can never miss each other.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, hermetic_env, native_goh_path, write

LIB = f'. "{REPO_ROOT}/gates/_proven.sh"; proven_settings'


def _bash(repo: Path, script: str, **env: str) -> str:
    r = subprocess.run(
        ["bash", "-c", f"{LIB}; {script}"], cwd=repo, capture_output=True, text=True,
        env=hermetic_env(drop_git=True, **env),
    )  # fmt: skip
    return r.stdout.strip()


def _repo(repo: Path) -> Path:
    write(repo, "src/lib.rs", "pub fn f() {}\n")
    write(repo, "crates/x/a.txt", "a\n")
    commit_all(repo)
    return repo


def _keys(repo: Path, native: bool, **env: str) -> tuple[str, str]:
    bin_env = {"GOH_RESOLVED_BIN": str(native_goh_path())} if native else {"GOH_RESOLVED_BIN": ""}
    tree = _bash(repo, 'proven_key "step one"', **bin_env, **env)
    scoped = _bash(repo, 'proven_scoped_key "step two" src crates/x nope', **bin_env, **env)
    return tree, scoped


def test_the_native_keys_are_the_bash_keys(repo: Path) -> None:
    _repo(repo)
    bash, native = _keys(repo, False), _keys(repo, True)
    assert bash[0] and bash[1], bash
    assert native == bash


def test_a_named_env_value_and_a_keep_file_key_the_same_way(repo: Path) -> None:
    _repo(repo)
    write(repo, ".gitignore", "secret.cfg\n")  # a keep file is an IGNORED file the build reads
    commit_all(repo)
    (repo / "secret.cfg").write_text("v1\n")
    env = {"GOH_PROVEN_ENV": "MY_KNOB", "MY_KNOB": "7", "GOH_EXPORT_KEEP": "secret.cfg"}
    assert _keys(repo, True, **env) == _keys(repo, False, **env)
    assert _keys(repo, True, **env) != _keys(repo, True, **{**env, "MY_KNOB": "8"})


def test_a_record_by_either_is_found_by_the_other(repo: Path) -> None:
    _repo(repo)
    for writer, reader in ((False, True), (True, False)):
        wenv = {"GOH_RESOLVED_BIN": str(native_goh_path()) if writer else ""}
        renv = {"GOH_RESOLVED_BIN": str(native_goh_path()) if reader else ""}
        step = f"interop {writer}"
        key, tree = _bash(repo, f'proven_key "{step}"', **wenv).split()
        _bash(repo, f'proven_record {key} {tree} "{step}" writer', **wenv)
        hit = _bash(repo, f'proven_lookup {key} "{step}" && echo HIT', **renv)
        assert hit.endswith("HIT") and "writer" in hit, (writer, reader, hit)


def test_a_dirty_tree_has_no_key_on_either_side(repo: Path) -> None:
    _repo(repo)
    (repo / "src" / "lib.rs").write_text("pub fn g() {}\n")
    assert _keys(repo, True) == ("", "") == _keys(repo, False)


def test_with_a_resolved_binary_the_wrapper_delegates(repo: Path, tmp_path: Path) -> None:
    """The bash functions hand the work to `goh proven` -- else the parity above compares bash with
    bash. A fake binary that answers "FAKE" is what the wrapper prints."""
    _repo(repo)
    fake = tmp_path / "goh"
    fake.write_text(
        '#!/bin/sh\n[ "$1 $2" = "proven key" ] && { cat >/dev/null; echo "FAKE TREE"; }\n'
    )
    fake.chmod(0o755)
    assert _bash(repo, 'proven_key "s"', GOH_RESOLVED_BIN=str(fake)) == "FAKE TREE"
