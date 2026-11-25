"""`goh land --check` runs the commit gate on each commit no commit gate judged.

GOH.CHERRYPICKGATE.

`git cherry-pick` runs no commit hook: an env-spread red from lane-tp.webvision3-uv (294d72e6)
was cherry-picked into a GOH-TINY lane and caught only at land. gates/commit_gate.sh stamps the
tree it passed (XX"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import hermetic_env
from timing_bounds import patience

from lib import commit_key, commit_stamp, land_check, land_check_cli, worker_share

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
    commit_stamp.record(commit_key.of(repo, "HEAD"))
    _git(repo, "cherry-pick", picked)
    return repo, _git(repo, "rev-parse", "HEAD")


def test_the_cherry_picked_commit_is_gated_and_named(lane) -> None:
    repo, picked = lane
    said: list[str] = []
    refused = commit_stamp.check(repo, said.append, GATE)
    assert len(refused) == 1 and picked[:10] in refused[0], refused
    assert "PLANTED_FINDING" in refused[0] and "the other lane's red" in refused[0], refused
    assert any(picked[:10] in s and "no commit gate stamped it" in s for s in said), said
    assert not any("gated through its hook" in s for s in said), "a stamped commit is not re-run"


def test_a_green_unstamped_commit_is_stamped_once_gated(lane) -> None:
    repo, _ = lane
    _commit(repo, "green.txt", "g\n", "unstamped, and green")
    assert commit_key.of(repo, "HEAD") not in commit_stamp.stamped()
    commit_stamp.check(repo, lambda _s: None, GATE)
    assert commit_key.of(repo, "HEAD") in commit_stamp.stamped()


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
    _git(repo, "commit", "-q", "--no-verify", "-m", "what was stamped")
    assert commit_key.of(repo, "HEAD") in commit_stamp.stamped()


# Each gate says where it runs and what it was handed, dirties its worktree, and waits for a second
# gate to be running beside it.
BESIDE = """
git diff --cached --name-only | tr '\\n' ' ' > "$LOG/$$.staged"
git status --porcelain --ignored | grep -v '^[ADM]  ' > "$LOG/$$.dirt"
pwd > "$LOG/$$.pwd"; git log -1 --format=%s > "$LOG/$$.head"
echo junk > left.txt; echo red > a.txt; echo a > .gitignore; git add .gitignore
touch "$LOG/$$.up"
[ -n "$WAIT" ] || exit 0
for i in $(seq 200); do [ "$(ls "$LOG" | grep -c up$)" -ge 2 ] && exit 0; sleep 0.1; done
echo alone; exit 1
"""


@pytest.mark.load_sensitive(reason="two gates wait for each other, at most 20 s")
def test_gates_run_side_by_side_each_in_a_worktree_moved_clean(lane, tmp_path, monkeypatch) -> None:
    repo, _ = lane
    _git(repo, "revert", "--no-edit", "HEAD")
    for i in range(4):
        _commit(repo, f"n{i}.txt", "n\n", f"n{i}")
    log = tmp_path / "log"
    log.mkdir()
    monkeypatch.setenv("LOG", str(log))
    monkeypatch.setenv("WAIT", "1")
    monkeypatch.setattr(commit_stamp, "width", lambda _c: 2)
    assert commit_stamp.check(repo, lambda _s: None, ["bash", "-c", BESIDE]) == []
    said = {k: sorted(p.read_text().strip() for p in log.glob(f"*.{k}"))
            for k in ("staged", "dirt", "pwd", "head")}  # fmt: skip
    assert said["staged"] == ["b.txt", "b.txt", "n0.txt", "n1.txt", "n2.txt", "n3.txt"], said
    assert len(set(said["pwd"])) == 2, "two workers, two worktrees, six commits"
    assert set(said["dirt"]) == {""}, f"what a gate left is not the next one's: {said['dirt']}"
    assert max(said["head"]).startswith("the other lane's"), said["head"]
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert not [p for p in said["pwd"] if Path(p).exists()]


def test_refusals_come_in_the_ranges_order_and_one_nobody_judged_is_no_green(
    lane, monkeypatch
) -> None:
    repo, picked = lane
    later = _commit(repo, "e.txt", "PLANTED_FINDING too\n", "later")
    monkeypatch.setattr(commit_stamp, "width", lambda _c: 4)
    refused = commit_stamp.check(repo, lambda _s: None, GATE)
    assert [r.split()[4] for r in refused] == [picked[:10], later[:10]], refused
    monkeypatch.setattr(commit_stamp, "_work", lambda *a: {})
    with pytest.raises(KeyError):
        commit_stamp.check(repo, lambda _s: None, GATE)


def test_a_commit_whose_worktree_cannot_be_put_is_refused(lane, monkeypatch) -> None:
    repo, picked = lane
    monkeypatch.setattr(commit_stamp._Seat, "_put", lambda *_a: "no worktree at its parent")
    (red,) = commit_stamp.check(repo, lambda _s: None, ["true"])
    assert picked[:10] in red and "no worktree at its parent" in red


def test_the_width_is_the_gates_share_never_the_machine(tmp_path, monkeypatch) -> None:
    asked = []

    def share_of(where, env, total, extra, mine=None):
        asked.append((where, total, extra, mine))
        return 3

    monkeypatch.setattr(commit_stamp.land_share, "share_of", share_of)
    monkeypatch.setenv("GOH_GATE_SLOT_DIR", str(tmp_path))
    monkeypatch.setenv("GOH_GATE_CORES", "16")
    monkeypatch.delenv(worker_share.ENV, raising=False)
    monkeypatch.setenv("GOH_SHARE_OWN", "1")
    assert commit_stamp.width("pre") == 3
    assert asked == [(str(tmp_path.resolve()), 16, 1, "pre")]
    monkeypatch.setenv(worker_share.ENV, "5")
    assert (commit_stamp.width("pre"), len(asked)) == (5, 1), "an admitted gate's own share"


def test_the_enqueue_asks_as_the_land_path_a_lanes_check_as_itself(tmp_path, monkeypatch) -> None:
    (tmp_path / ".gatesrc").write_text("")
    asked = []
    monkeypatch.setattr(commit_stamp, "check", lambda *a, **k: asked.append(k) or [])
    for gated in (True, False):
        land_check_cli._commits(str(tmp_path), print, gated)
    assert asked == [{"gate_class": "pre"}, {"gate_class": None}]


@pytest.mark.load_sensitive(reason="the two halves wait for each other, at most patience(20) s")
def test_the_commits_gates_run_beside_the_rest_of_the_check(tmp_path, monkeypatch) -> None:
    from lib import fragment_check

    both = threading.Barrier(2, timeout=patience(20))
    monkeypatch.setattr(commit_stamp, "check", lambda *a, **k: [both.wait(), "x"][1:])
    monkeypatch.setattr(fragment_check, "missing", lambda top: ["T.I has no fragment"])
    said: list[str] = []
    red = land_check_cli.tip(
        str(tmp_path), said.append, said.append, lambda: [both.wait(), "a guard"][1:]
    )
    assert red == ["x", "the changelog fragment of T.I", "a guard"], "every refusal, in one pass"
