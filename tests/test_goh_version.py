"""lib/goh_version.sh: goh's version is its release TAG, by one rule (GOH.SHIPRELREUSE).

The release commit used to bump Cargo.toml, which every crate compiles in (CARGO_PKG_VERSION), so it
changed what the tip's land verdict had judged. Now: a commit tagged vX.Y.Z is X.Y.Z, a commit past
one is git describe's `X.Y.Z-N-g<sha>` (what it is, honestly), no reachable release tag is
`unknown` -- never a made-up release number -- and only a v + three numbers tag is a release.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, git

LIB = REPO_ROOT / "lib" / "goh_version.sh"


def _ask(repo: Path, *args: str) -> str:
    argv = ["bash", str(LIB), *args, str(repo), "HEAD"]
    return subprocess.run(argv, capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip


def _commit(repo: Path, msg: str) -> str:
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
        "-m", msg)  # fmt: skip
    return git(repo, "rev-parse", "HEAD").strip()


def test_no_release_tag_is_unknown_never_a_made_up_number(repo: Path) -> None:
    git(repo, "tag", "vnext")  # not a release: v + three numbers only
    assert _ask(repo) == "unknown"
    assert _ask(repo, "--release") == ""


def test_a_tagged_commit_is_its_release(repo: Path) -> None:
    git(repo, "tag", "-a", "v1.2.3", "-m", "v1.2.3")
    assert _ask(repo) == "1.2.3"
    assert _ask(repo, "--release") == "v1.2.3"


def test_a_commit_past_a_release_says_what_it_is(repo: Path) -> None:
    git(repo, "tag", "v1.2.3")
    sha = _commit(repo, "next")
    got = _ask(repo)
    assert got.startswith("1.2.3-1-g") and sha.startswith(got.split("-g")[1]), got
    assert _ask(repo, "--release") == ""


def test_a_hook_repository_does_not_answer_for_the_checkout(repo: Path, tmp_path: Path) -> None:
    """A gate run inside another repo's hook inherits its GIT_DIR (contract #12)."""
    git(repo, "tag", "v1.2.3")
    from _fast_git import fast_init
    from conftest import hermetic_env

    other = tmp_path / "other"
    other.mkdir()
    fast_init(other, "main")

    env = hermetic_env(GIT_DIR=str(other / ".git"))
    got = subprocess.run(["bash", str(LIB), str(repo), "HEAD"], capture_output=True, text=True,
                         env=env, check=True, stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip
    assert got == "1.2.3"
