"""`goh land --check` runs the commit gate on each commit no commit gate judged.

GOH.CHERRYPICKGATE.

`git cherry-pick` runs no commit hook: an env-spread red from lane-tp.webvision3-uv (294d72e6)
was cherry-picked into a GOH-TINY lane and caught only at land. gates/commit_gate.sh stamps the
tree it passed (lib/commit_stamp.py); the land's check runs the gate, as that commit's pre-commit
would have, on every commit of the range whose tree carries no stamp, and names it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import hermetic_env

from lib import commit_stamp, land_check

# The fake commit gate: red on a staged change that carries the word, as a real finding would be.
GATE = [
    "bash",
    "-c",
    "git diff --cached | grep -q PLANTED_FINDING || exit 0; echo 'found PLANTED_FINDING'; exit 1",
]


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, env=hermetic_env(drop_git=True),
                          stdin=subprocess.DEVNULL)  # fmt: skip
    return done.stdout.strip()


def _commit(repo: Path, name: str, text: str, msg: str) -> str:
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "--no-verify", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def lane(tmp_path, monkeypatch) -> tuple[Path, str]:
    """main; a lane whose own commit a gate stamped; a commit cherry-picked from another branch."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for k in [k for k in __import__("os").environ if k.startswith("GIT_")]:
        monkeypatch.delenv(k)
    repo = tmp_path / "repo"
    fast_init(repo, "main")
    for kv in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", "/dev/null")):
        _git(repo, "config", *kv)
    _commit(repo, "a.txt", "a\n", "base")
    _git(repo, "switch", "-q", "-c", "other")
    picked = _commit(repo, "b.txt", "PLANTED_FINDING\n", "the other lane's red")
    _git(repo, "switch", "-q", "-c", "lane", "main")
    _commit(repo, "c.txt", "c\n", "gated through its hook")
    commit_stamp.record(_git(repo, "rev-parse", "HEAD^{tree}"), str(repo))
    _git(repo, "cherry-pick", picked)
    return repo, _git(repo, "rev-parse", "HEAD")


def test_the_cherry_picked_commit_is_gated_and_named(lane) -> None:
    repo, picked = lane
    said: list[str] = []
    refused = commit_stamp.check(repo, said.append, GATE)
    assert len(refused) == 1 and picked[:10] in refused[0], refused
    assert "PLANTED_FINDING" in refused[0] and "the other lane's red" in refused[0], refused
    assert any(picked[:10] in s and "no commit gate stamped" in s for s in said), said
    assert not any("gated through its hook" in s for s in said), "a stamped commit is not re-run"


def test_a_green_unstamped_commit_is_stamped_once_gated(lane) -> None:
    repo, _ = lane
    _git(repo, "revert", "--no-edit", "HEAD")  # unstamped, and green
    commit_stamp.check(repo, lambda _s: None, GATE)
    assert _git(repo, "rev-parse", "HEAD^{tree}") in commit_stamp.stamped()


def test_land_check_refuses_naming_the_commit(lane, monkeypatch) -> None:
    repo, _ = lane
    monkeypatch.setattr(land_check, "run", lambda *a, **k: [])
    monkeypatch.setattr(land_check.cov_risk, "say", lambda *a, **k: None)
    monkeypatch.setattr(commit_stamp, "GATE_ARGV", GATE, raising=False)
    assert land_check.cli(str(repo), repo / "q") == 1


def test_record_stamps_the_index_being_committed(lane) -> None:
    repo, _ = lane
    (repo / "d.txt").write_text("d\n")
    _git(repo, "add", "d.txt")
    assert commit_stamp.main(["record", str(repo)]) == 0
    import run_path

    assert run_path.index_tree(str(repo)) in commit_stamp.stamped()
