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


def _inputs(repo: Path) -> str:
    """goh's four build inputs, committed: what the stamp names the trees of."""
    (repo / "crates").mkdir()
    (repo / "crates" / "a.rs").write_text("fn main() {}\n")
    for name in ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml"):
        (repo / name).write_text(f"# {name}\n")
    git(repo, "add", "-A")
    _commit(repo, "inputs")
    specs = [f"HEAD:{i}" for i in ("crates", "Cargo.toml", "Cargo.lock", "rust-toolchain.toml")]
    return " ".join(git(repo, "rev-parse", *specs).split())


def test_the_stamp_is_the_trees_on_an_untagged_head_and_names_the_tag_on_a_tagged_one(
    repo: Path,
) -> None:
    """GOH.PROVENTAGHEAD: ONE formula, asked by the resolver, build-goh.sh and the tests alike. A
    trees-only copy in the tests judged every fake on a tagged HEAD stale (17 red on v0.126.0)."""
    trees = _inputs(repo)
    assert _ask(repo, "--stamp") == trees
    git(repo, "tag", "v1.2.3")
    assert _ask(repo, "--stamp") == f"{trees} v1.2.3"
    _commit(repo, "past the release")  # the same trees, past the tag: the untagged build again
    assert _ask(repo, "--stamp") == trees


def test_a_stamp_built_as_a_release_names_that_release_and_none_is_empty(repo: Path) -> None:
    """build-goh.sh --as-release asks the same function with the release it will be; a commit with
    no build inputs has no stamp (nothing to compare), never a half one."""
    assert _ask(repo, "--stamp") == ""
    trees = _inputs(repo)
    got = subprocess.run(
        ["bash", "-c", '. "$1"; goh_stamp_at "$2" HEAD v9.9.9', "_", str(LIB), str(repo)],
        capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
    ).stdout  # fmt: skip
    assert got == f"{trees} v9.9.9"


def _embedding(repo: Path) -> None:
    """A crate that embeds two files outside crates/ -- one call on one line, one split over three
    (rustfmt's shape for a long path) -- and a test module that embeds a fixture."""
    _inputs(repo)
    src = repo / "crates" / "k" / "src"
    src.mkdir(parents=True)
    (src / "lib.rs").write_text(
        'const SHAPES: &str = include_str!("../../../lib/shapes.json");\n'
        "const TOOLS: &[u8] = include_bytes!(\n"
        '    "../../../gates/tools.tsv"\n'
        ");\n"
        "#[cfg(test)]\n"
        "mod lib_tests;\n"
    )  # fmt: skip
    (src / "lib_tests.rs").write_text('const F: &str = include_str!("../../../tests/f.txt");\n')
    for rel in ("lib/shapes.json", "gates/tools.tsv", "tests/f.txt"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("one\n")
    git(repo, "add", "-A")
    _commit(repo, "embeds")


def _edit(repo: Path, rel: str) -> None:
    (repo / rel).write_text("two\n")
    git(repo, "add", "-A")
    _commit(repo, f"edit {rel}")


def test_a_file_the_binary_embeds_moves_the_stamp_alone(repo: Path) -> None:
    """GOH.STAMPINPUTS: lib/busyloop_shapes.json and gates/required_tools.tsv are include_str!'d by
    the goh binary but were not stamp inputs, so an edit to either alone left the stamp -- and the
    store's stale build -- standing: the gates judged with the old shapes or tool manifest."""
    _embedding(repo)
    for rel in ("lib/shapes.json", "gates/tools.tsv"):
        before = _ask(repo, "--stamp")
        _edit(repo, rel)
        assert _ask(repo, "--stamp") != before, f"an edit to the embedded {rel} left the stamp"


def test_a_test_modules_fixture_is_not_a_build_input(repo: Path) -> None:
    """A `*_tests.rs` module compiles only under cfg(test): its fixture is not in the binary, so an
    edit to it must not rebuild the store."""
    _embedding(repo)
    before = _ask(repo, "--stamp")
    _edit(repo, "tests/f.txt")
    assert _ask(repo, "--stamp") == before


def test_the_inputs_are_the_four_roots_then_every_embedded_path(repo: Path) -> None:
    _embedding(repo)
    got = _ask(repo, "--inputs").split("\n")
    assert got == ["crates", "Cargo.toml", "Cargo.lock", "rust-toolchain.toml",
                   "gates/tools.tsv", "lib/shapes.json"]  # fmt: skip
