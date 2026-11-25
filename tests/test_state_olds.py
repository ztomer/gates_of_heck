"""lib/state_olds.py (GOH.OLDWRITERS, L12): what still runs goh without a commit. A checkout that
went back and returned ran old code in between, so a process counts from the start of the CURRENT
span with the commit; an installed binary is asked for its build stamp."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import state_olds
from _tree_clone import from_template
from conftest import git, hermetic_env
from state_olds import Checkout, Proc

T0 = 1_790_000_000


def commit(repo: Path, at: int, msg: str, *args: str) -> str:
    env = hermetic_env(GIT_COMMITTER_DATE=f"{at} +0000", GIT_AUTHOR_DATE=f"{at} +0000")
    ident = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    argv = args or ("commit", "-q", "--allow-empty", "-m", msg)
    subprocess.run(["git", "-C", str(repo), *ident, *argv],
                   check=True, env=env, capture_output=True, stdin=subprocess.DEVNULL)  # fmt: skip
    return git(repo, "rev-parse", "HEAD").strip()


def _build_checkout(root: Path) -> None:
    root.mkdir(exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "core.logAllRefUpdates", "always")  # its reflog is the subject
    for at, msg in ((0, "before"), (100, "x"), (200, "after")):
        commit(root, T0 + at, msg)


@pytest.fixture
def co(tmp_path_factory, tmp_path) -> Checkout:
    """A checkout: `before` at T0, X at T0+100, `after` at T0+200."""
    root = from_template(tmp_path_factory, "state-olds", _build_checkout, tmp_path / "goh")
    return Checkout(root, git(root, "rev-parse", "HEAD~1").strip())


def ran(co: Checkout, *starts: int, where: str = "lib/a.py", base: str = "") -> list[Proc]:
    return [Proc(i, T0 + s, f"python3 {base or co.root}/{where}") for i, s in enumerate(starts, 10)]


def lines(co: Checkout, procs: list[Proc], open_by: set[int] = frozenset()) -> list[str]:
    return state_olds.olds(co, procs, set(open_by), found=[])


def test_a_process_from_before_its_checkout_had_it(co) -> None:
    got = lines(co, ran(co, 50, 150, 250))
    assert len(got) == 1 and got[0].startswith("process 10: started before"), got


def test_a_rollback_and_return_is_seen(co) -> None:
    """L12: the checkout stood at X (T0+100), went back (T0+300) and returned (T0+400). A process
    from T0+350 ran code without X, and "first stood at X" (T0+100) would pass it."""
    commit(co.root, T0 + 300, "", "checkout", "-q", "--detach", "HEAD~2")
    commit(co.root, T0 + 400, "", "checkout", "-q", "main")
    got = lines(Checkout(co.root, co.x), ran(co, 150, 350, 450))
    assert [g.split(":")[0] for g in got] == ["process 10", "process 11"], got


def test_a_checkout_that_went_back_and_stayed(co) -> None:
    commit(co.root, T0 + 300, "", "checkout", "-q", "--detach", "HEAD~2")
    got = lines(Checkout(co.root, co.x), ran(co, 350))
    assert any(g.startswith("process 10: checkout") for g in got), got
    assert any(g.startswith(f"worktree {co.root}") for g in got), got


def test_a_reflog_that_does_not_reach_head_proves_nothing(co) -> None:
    log = co.root / ".git" / "logs" / "HEAD"
    log.write_text("\n".join(log.read_text().splitlines()[:-1]) + "\n")
    assert "does not say" in lines(co, ran(co, 900))[0]
    log.unlink()
    assert "does not say" in lines(co, ran(co, 900))[0]


def test_an_export_a_worktree_and_a_holder(co, tmp_path) -> None:
    old = git(co.root, "rev-parse", "HEAD~2").strip()
    lane = tmp_path / "lane"
    git(co.root, "worktree", "add", "-q", "--detach", str(lane), old)
    head = "/c/goh/head"
    procs = [Proc(1, T0 + 900, f"python3 {head}/{old}/lib/a.py"),
             Proc(2, T0 + 900, f"python3 {head}/{co.x}/lib/a.py"),
             Proc(3, T0 + 900, f"python3 {head}/{'0' * 40}/lib/a.py"),
             Proc(4, T0 + 900, f"python3 {lane}/lib/a.py"),
             Proc(5, T0 + 900, "python3 -c 'import goh_db'"),
             Proc(6, T0 + 900, "vim notes"),
             Proc(os.getpid(), T0, f"python3 {co.root}/lib/state_olds.py")]  # fmt: skip
    got = lines(co, procs, {2, 5})
    assert [g.split(":")[0] for g in got] == ["process 1", "process 3", "process 4", "holder 5",
                                              f"worktree {lane}"], got  # fmt: skip


SHA = ("a" * 40, "b" * 40, "c" * 40)


def test_an_installed_binary_answers_by_its_build_stamp(co, tmp_path, monkeypatch) -> None:
    """Before X's build: named. Built at a commit that has X, released or not: passed. One that
    says nothing, or an uncommitted build, proves nothing."""
    monkeypatch.setattr(Checkout, "stamps", lambda _s: iter([SHA[1], SHA[2]]))
    b = tmp_path / "bin" / "goh"
    b.parent.mkdir()
    b.touch()
    for said, ok in ((SHA[0], False), (SHA[1], True), (f"{SHA[2]} v1.2.3", True), ("dirty", False),
                     ("", False)):  # fmt: skip
        got = state_olds.olds(co, [], set(), [b], lambda _b, said=said: said)
        assert (got == []) == ok, (said, got)
        assert ok or got[0].startswith(f"binary {b}: its build stamp")


def test_a_running_binary_replaced_on_disk(co, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(Checkout, "stamps", lambda _s: iter([SHA[1]]))
    b = tmp_path / "bin" / "goh"
    b.parent.mkdir()
    b.touch()
    os.utime(b, (T0 + 500, T0 + 500))
    procs = [Proc(7, T0 + 400, f"{b} land"), Proc(8, T0 + 600, f"{b} land")]
    assert b in state_olds.binaries(co.root, procs)
    got = state_olds.olds(co, procs, set(), [b], lambda _b: SHA[1])
    assert len(got) == 1 and got[0].startswith("binary 7: "), got


def test_the_stamps_are_those_of_the_commits_with_it(co) -> None:
    """The house script's own: each change of a build input since X, HEAD and X; none before."""
    root = co.root
    (root / "lib").mkdir()
    (root / "lib" / "goh_version.sh").write_text(
        'case "$1" in --inputs) echo crates ;; *) git -C "$2" rev-parse "$3:crates" ;; esac\n'
    )
    made = []
    (root / "crates").mkdir()
    for i in range(4):
        (root / "crates" / "a").write_text(str(i))
        git(root, "add", "-A")
        made.append(commit(root, T0 + i, str(i)))
    trees = [git(root, "rev-parse", f"{c}:crates").strip() for c in made]
    got = set(Checkout(root, made[1]).stamps())
    assert got == set(trees[1:])


def test_the_real_collectors_answer() -> None:
    procs = state_olds.processes()
    assert any(p.pid == os.getpid() for p in procs) and all(p.start > 0 for p in procs)
    assert isinstance(state_olds.holders(), set)
