"""A template shared by many tests is CLONED per test, and each clone is its own (GOH.FIXTURECOST).

Fixtures that build the same repository for every test build it ONCE per worker and hand each test
a `clone_tree` copy: one clonefile(2) call on APFS (copy-on-write, ~2 ms for lib/ against ~65 ms for
shutil.copytree), copytree elsewhere. The speed is only worth having if the copies are still
isolated: what one test writes -- a file, a commit, a config key, a hook -- no other test sharing
the template may see, and the template itself never changes.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import _tree_clone
import pytest
from _fast_git import fast_init
from _tree_clone import clone_tree, copytree_clone, from_template, process_template, template_of
from conftest import commit_all, git, write


def _state(r: Path) -> tuple:
    """Everything a test could have written that another test could read."""
    return (
        (r / "README.md").read_text(),
        sorted(p.name for p in r.iterdir()),
        git(r, "rev-parse", "HEAD").strip(),
        git(r, "status", "--porcelain"),
        git(r, "config", "--local", "--list"),
        sorted(p.name for p in (r / ".git" / "hooks").iterdir()),
    )


def _write_everything(r: Path) -> None:
    write(r, "README.md", "# changed by the first test\n")
    write(r, "new.txt", "only in the first clone\n")
    git(r, "add", "-A")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "first test's commit")
    git(r, "config", "goh.mark", "first")
    (r / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\nexit 0\n")


@pytest.mark.parametrize("clone", [clone_tree, copytree_clone], ids=["clonefile", "copytree"])
def test_two_clones_of_one_template_never_see_each_others_writes(
    _repo_template: Path, tmp_path: Path, clone
) -> None:
    """The `repo` fixture's own template, cloned as two tests get it: the first writes everything a
    test can, and the second clone and the template read exactly what they did before."""
    first, second = tmp_path / "first", tmp_path / "second"
    clone(_repo_template, first)
    clone(_repo_template, second)
    before = _state(second)
    assert before == _state(_repo_template)
    _write_everything(first)
    assert _state(first) != before  # the writes landed (the comparison can fail)
    assert _state(second) == before
    assert _state(_repo_template) == before
    # ...and the other direction: the second's own writes do not reach the first.
    write(second, "second.txt", "x\n")
    assert not (first / "second.txt").exists()


def test_a_clone_onto_an_existing_path_is_refused(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "dst").mkdir()
    with pytest.raises(FileExistsError):
        clone_tree(src, tmp_path / "dst")


def test_the_clone_of_a_committed_template_is_a_working_repository(
    _repo_template: Path, tmp_path: Path
) -> None:
    r = tmp_path / "r"
    clone_tree(_repo_template, r)
    assert git(r, "rev-parse", "--show-toplevel").strip() == str(r.resolve())
    done = subprocess.run(["git", "-C", str(r), "fsck", "--no-progress"], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="clonefile(2) is the macOS path")
def test_on_macos_the_clone_is_clonefile_not_a_silent_copytree(
    _repo_template: Path, tmp_path: Path, monkeypatch
) -> None:
    """A fallback that always ran would pass every isolation test and save nothing."""

    def refuse(*_a, **_k):
        raise AssertionError("clone_tree fell back to copytree on the suite's own temp volume")

    monkeypatch.setattr(_tree_clone, "copytree_clone", refuse)
    clone_tree(_repo_template, tmp_path / "r")
    assert (tmp_path / "r" / "README.md").read_text() == "# fixture\n"


def test_from_template_builds_once_and_each_caller_gets_its_own_tree(
    tmp_path_factory, tmp_path: Path
) -> None:
    """Two tests asking `from_template` for one key: one build, two trees, no shared writes."""
    builds: list[Path] = []

    def build(tree: Path) -> None:
        builds.append(tree)
        tree.mkdir()
        fast_init(tree, "main")
        write(tree, "README.md", "# fixture\n")
        commit_all(tree)

    key = f"isolation-{tmp_path.name}"
    first = from_template(tmp_path_factory, key, build, tmp_path / "first")
    second = from_template(tmp_path_factory, key, build, tmp_path / "second")
    assert len(builds) == 1
    before = _state(second)
    _write_everything(first)
    assert _state(first) != before
    assert _state(second) == before
    assert _state(template_of(tmp_path_factory, key)) == before


@pytest.mark.parametrize("where", ["inside", "beside"])
def test_a_template_that_names_its_own_directory_is_refused(
    tmp_path_factory, tmp_path: Path, where: str
) -> None:
    """A remote pointing INTO the template, or at a bare origin built BESIDE it, would hand every
    clone the one shared repository to push to."""

    def build(tree: Path) -> None:
        tree.mkdir()
        fast_init(tree)
        origin = tree / "origin.git" if where == "inside" else tree.parent / "origin.git"
        git(tree, "remote", "add", "origin", str(origin))

    with pytest.raises(ValueError, match="names its own directory"):
        from_template(tmp_path_factory, f"self-ref-{where}-{tmp_path.name}", build, tmp_path / "r")


def test_process_template_builds_once_and_clones_stay_apart(tmp_path: Path) -> None:
    """The factory-free template (`_clean_checkout_of_todays_gates`'s): one build per process, and
    a clone of it is as private as any other."""
    builds: list[Path] = []

    def build(tree: Path) -> None:
        builds.append(tree)
        fast_init(tree, "main")
        write(tree, "README.md", "# fixture\n")
        commit_all(tree)

    key = f"proc-{tmp_path.name}"
    one, two = process_template(key, build), process_template(key, build)
    assert one == two and len(builds) == 1
    clone_tree(one, tmp_path / "a")
    clone_tree(one, tmp_path / "b")
    before = _state(tmp_path / "b")
    _write_everything(tmp_path / "a")
    assert _state(tmp_path / "b") == before == _state(one)


def _git_sees(tree: Path, key: str) -> str:
    """What a git the build spawned would do about background work: the effective values."""
    from conftest import hermetic_env

    out = []
    for env in (hermetic_env(drop_git=True), hermetic_env(), None):
        run = subprocess.run(
            ["git", "-C", str(tree), "config", "--get", key],
            capture_output=True, text=True, env=env, check=False, stdin=subprocess.DEVNULL,
        )  # fmt: skip
        out.append(run.stdout.strip())
    return "/".join(out)


@pytest.mark.parametrize("via", ["template", "process_template"])
def test_every_git_a_template_build_spawns_has_background_work_off(
    tmp_path_factory, tmp_path: Path, via: str
) -> None:
    """GOH.TEMPLATEQUIET (2026-11-18): a commit detaches `git maintenance run`, which takes and
    drops .git/objects/maintenance.lock while the template is scanned and cloned (a ship's
    instrumented run died on FileNotFoundError, the 2026-10-08 race again). The build runs with
    auto-maintenance and auto-gc off for EVERY git it spawns, through any env helper
    (`hermetic_env(drop_git=True)` drops GIT_CONFIG_*), and no template site sets it by hand.
    """
    seen: dict[str, str] = {}

    def build(tree: Path) -> None:
        tree.mkdir()
        fast_init(tree, "main")
        write(tree, "README.md", "# fixture\n")
        commit_all(tree)
        seen["maintenance"] = _git_sees(tree, "maintenance.auto")
        seen["gc"] = _git_sees(tree, "gc.auto")

    key = f"quiet-{via}-{tmp_path.name}"
    if via == "template":
        _tree_clone.template(tmp_path_factory, key, build)
    else:
        process_template(key, build)
    assert seen == {"maintenance": "false/false/false", "gc": "0/0/0"}
    # ... and only for the build: a test's own git is not the template's.
    assert _git_sees(tmp_path, "maintenance.auto") != "false/false/false"


@pytest.mark.load_sensitive
def test_a_template_is_quiescent_before_it_is_scanned_or_cloned(
    tmp_path_factory, tmp_path: Path
) -> None:
    """Whatever a build leaves running in its tree (here a detached process holding a lock for
    0.4 s, as `git maintenance run` does) has finished when the template is handed out."""

    def build(tree: Path) -> None:
        tree.mkdir()
        fast_init(tree, "main")
        lock = tree / ".git" / "objects" / "maintenance.lock"
        # The shell backgrounds the holder and exits, as git detaches `maintenance run`.
        script = f"( touch '{lock}'; sleep 0.4; rm -f '{lock}' ) </dev/null >/dev/null 2>&1 &"
        subprocess.run(["sh", "-c", script], check=True, stdin=subprocess.DEVNULL)
        while not lock.exists():
            time.sleep(0.005)

    built = _tree_clone.template(tmp_path_factory, f"quiesce-{tmp_path.name}", build)
    assert not (built / ".git" / "objects" / "maintenance.lock").exists()
