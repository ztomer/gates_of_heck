"""tests/_quiet_git.py makes what git makes: each scenario is run by both, at one date, and every
object, the branch, the index and the work tree's state must be the same."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import _quiet_git
import pytest
from _fast_git import fast_init
from _tree_clone import clone_tree, from_template
from conftest import hermetic_env

DATE = "1790000000 +0200"


def _env(real: bool) -> dict[str, str]:
    env = hermetic_env(True, GIT_AUTHOR_DATE=DATE, GIT_COMMITTER_DATE=DATE)
    return {**env, _quiet_git.REAL: "1"} if real else env


def _build_empty(d: Path) -> None:
    fast_init(d, "main")


@pytest.fixture
def made(tmp_path_factory, tmp_path: Path):
    """An empty repository at tmp_path/NAME: a clone of the run's."""
    return lambda name: from_template(tmp_path_factory, "quiet-empty", _build_empty,
                                      tmp_path / name)  # fmt: skip


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       env=env or _env(True), stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout


def _first(d: Path) -> list[tuple[str, ...]]:
    (d / "a.txt").write_text("one\n")
    (d / "sub" / "deep").mkdir(parents=True)
    (d / "sub" / "deep" / "b").write_bytes(os.urandom(0) + b"\0\xff" * 70_000)
    (d / "sub.x").write_text("sorts between sub and sub/\n")
    (d / "run.sh").write_text("#!/bin/sh\n")
    (d / "run.sh").chmod(0o755)
    (d / "link").symlink_to("a.txt")
    (d / "naïve name").write_text("not ascii, a space\n")
    (d / ".gitignore").write_text("skipped\n")
    (d / "skipped").write_text("never added\n")
    return [("add", "-A"), ("commit", "-qm", "first  \n\n\n\nsecond paragraph \n")]


def _edits(d: Path) -> list[tuple[str, ...]]:
    (d / "a.txt").write_text("two\n")
    (d / "sub.x").unlink()
    (d / "run.sh").chmod(0o644)
    (d / "left").write_text("untracked\n")
    return [("commit", "-q", "-a", "-m", "tracked only", "-m", "a body")]


def _staged(d: Path) -> list[tuple[str, ...]]:
    (d / "new").write_text("n\n")
    (d / "other").write_text("stays untracked\n")
    (d / "a.txt").write_text("three\n")
    return [("add", "--", "new", "sub"), ("commit", "-q", "--no-verify", "-m", "one path")]


def _nothing(d: Path) -> list[tuple[str, ...]]:
    return [("add", "."), ("-c", "user.name=An Other", "-c", "user.email=o@o", "commit", "-q",
                           "--allow-empty", "-m", "empty")]  # fmt: skip


STEPS = (_first, _edits, _staged, _nothing)


def _state(d: Path) -> dict[str, str]:
    return {" ".join(a): _git(d, *a) for a in (
        ("log", "--format=%H %T %P %an %ae %at %cn %ce %ct%n%B", "main"), ("ls-files", "-s"),
        ("status", "--porcelain=v2", "--branch"), ("ls-tree", "-r", "HEAD"),
        ("diff", "HEAD", "--stat"))}  # fmt: skip


def test_every_step_is_gits_own_byte_for_byte(made, monkeypatch) -> None:
    mine, gits = made("mine"), made("gits")
    served: list[bool] = []
    real = _quiet_git.serve
    monkeypatch.setattr(_quiet_git, "serve", lambda *a: served.append(real(*a)) or served[-1])
    for step in STEPS:
        for d in (mine, gits):
            for args in step(d):
                served.clear()
                _git(d, *args, env=_env(d == gits))
                assert served[-1] == (d == mine), (step.__name__, args)
        assert _state(mine) == _state(gits), step.__name__
    _git(mine, "fsck", "--strict", "--full")
    assert not list((mine / ".git" / "objects").glob("??")), "a loose object"
    left = {p.name for p in (mine / ".git").iterdir()}
    assert left <= {"HEAD", "config", "hooks", "info", "index", "objects", "refs"}, left
    (mine / "a.txt").write_text("git goes on from there\n")
    _git(mine, "commit", "-qam", "git's own")
    assert _git(mine, "log", "--format=%s").split("\n")[0] == "git's own"


def test_a_branch_moves_where_it_is(repo: Path) -> None:
    """The template's branch is in packed-refs: no refs/heads is made for it."""
    (repo / "f").write_text("1\n")
    root, env = str(repo.resolve().parent), _env(False)
    for args in (["git", "add", "f"], ["git", "commit", "-qm", "c"]):
        assert _quiet_git.serve(args, repo, env, root)
    assert not (repo / ".git" / "refs" / "heads").exists()
    assert _git(repo, "log", "--format=%s") == "c\nfixture\n"
    assert _git(repo, "rev-parse", "main") == _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("settings", [
    ("user.email", "t@t", "user.name", "t", "user.email", "u@u"),
    ("core.hooksPath", ".githooks", "core.bare", "false"),
    ("gc.auto", "0", "User.Name", "x", "user.name", "Y"),
])  # fmt: skip
def test_a_setting_is_written_as_git_writes_it(made, settings) -> None:
    mine, gits = made("mine"), made("gits")
    for key, value in zip(settings[::2], settings[1::2], strict=True):
        assert _quiet_git.serve(["git", "config", key, value], mine, _env(False),
                                str(mine.resolve().parent))  # fmt: skip
        _git(gits, "config", key, value)
        a, b = ((d / ".git" / "config").read_text() for d in (mine, gits))
        assert a == b, (key, value)


@pytest.mark.parametrize("args", [
    ("--add", "a.b", "c"), ("a.b",), ("--get", "a.b"), ("remote.o.url", "/x"), ("a.b", "two words"),
    ("a.b", "-x"), ("a.b", "#x"), ("alias.st", ""),
])  # fmt: skip
def test_any_other_config_is_gits(made, args) -> None:
    d = made("r")
    argv = ["git", "config", *args]
    assert not _quiet_git.serve(argv, d, _env(False), str(d.resolve().parent))


def _hook(d: Path) -> None:
    (d / ".git" / "hooks").mkdir(exist_ok=True)
    (d / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    (d / ".git" / "hooks" / "pre-commit").chmod(0o755)


def _attributes(d: Path) -> None:
    (d / ".gitattributes").write_text("* text eol=crlf\n")


def _conf(*args: str):
    return lambda d: _git(d, "config", *args)


def _nested(d: Path) -> None:
    (d / "nested").mkdir()
    clone_tree(d / ".git", d / "nested" / ".git")


@pytest.mark.parametrize("plant", [
    _hook, _attributes, _conf("core.hooksPath", "/nowhere"), _conf("core.autocrlf", "input"),
    _conf("commit.gpgsign", "true"), _conf("core.logAllRefUpdates", "always"),
    lambda d: _git(d, "update-index", "--assume-unchanged", "f"),
    lambda d: _git(d, "checkout", "-q", "--detach"),
    lambda d: (d / ".git" / "MERGE_HEAD").write_text("0" * 40),
    _nested, lambda d: (d / ".git" / "index.lock").touch(),
    lambda d: (d / ".git" / "objects").chmod(0o555),
    lambda d: (d / ".git" / "packed-refs.lock").touch(),
], ids=["hook", "attributes", "hookspath", "autocrlf", "gpgsign", "reflog", "flagged", "detached",
        "merge", "nested", "locked", "sealed", "refs-locked"])  # fmt: skip
def test_what_git_would_judge_is_gits(tmp_path: Path, made, plant) -> None:
    env = _env(False)
    made("r")
    (tmp_path / "r" / "f").write_text("1\n")

    def serve(*args: str) -> bool:
        return _quiet_git.serve(["git", *args], tmp_path / "r", env, str(tmp_path.resolve()))

    assert serve("add", "-A") and serve("commit", "-qm", "c"), "the repository is served at all"
    (tmp_path / "r" / "f").write_text("2\n")
    plant(tmp_path / "r")
    (tmp_path / "r" / "g").write_text("new\n")
    before = {p: p.read_bytes() for p in (tmp_path / "r" / ".git").rglob("*") if p.is_file()}
    assert not (serve("add", "-A") and serve("commit", "-qm", "c")), "the second was served"
    moved = {p for p, b in before.items() if p.read_bytes() != b}
    assert not {p for p in moved if p.name != "index"}, "a decline wrote"


@pytest.mark.parametrize("argv", [
    ("add",), ("add", "-f", "f"), ("add", "*.x"), ("add", "absent"), ("add", "-p"),
    ("commit", "-m", "its summary is read"), ("commit", "-qm", ""), ("commit", "-qm", "nothing"),
    ("commit", "-q", "--amend", "-m", "x"),
    ("-c", "core.abbrev=4", "add", "-A"), ("status",),
])  # fmt: skip
def test_an_option_or_a_refusal_is_gits(tmp_path: Path, made, argv) -> None:
    d, root, env = made("r"), str(tmp_path.resolve()), _env(False)
    (d / "f").write_text("1\n")
    for first in (["git", "add", "f"], ["git", "commit", "-qm", "c"]):
        assert _quiet_git.serve(first, d, env, root)
    assert not _quiet_git.serve(["git", *argv], d, env, root)


def test_another_git_another_root_or_a_git_variable_is_gits(tmp_path: Path, made) -> None:
    d, root, env, argv = made("r"), str(tmp_path.resolve()), _env(False), ["git", "add", "-A"]
    (d / "f").write_text("1\n")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "git").write_text("#!/bin/sh\n")
    (tmp_path / "bin" / "git").chmod(0o755)
    other = {**env, "PATH": f"{tmp_path / 'bin'}:{env['PATH']}"}
    assert not _quiet_git.serve(argv, d, other, root)
    assert not _quiet_git.serve(argv, d, {**env, "GIT_INDEX_FILE": "x"}, root)
    assert not _quiet_git.serve(argv, d, env, str(tmp_path / "elsewhere"))
    assert not _quiet_git.serve("git add -A", d, env, root)
    assert _quiet_git.serve(argv, d, env, root)
