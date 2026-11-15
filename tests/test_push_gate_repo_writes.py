"""A gate run never writes the repository it gates (BACKLOG 6.11).

The games metarepo's `origin` read `https://elsewhere.invalid/x.git` from 2026-10-08 until it was
repaired by hand: a probe in that commit's own hook run wrote it into the REAL `.git/config`, and
nothing compared the config before and after. The push gate now snapshots the repository's config
and refs before the gate and reads them again after it (lib/repo_writes.sh): a config write refuses
the push naming the key, a moved ref is named and not refused (another session commits during a long
gate), and a token in a URL is redacted from the report.
"""

from pathlib import Path

from test_push_gate import GATE, _git, _repo
from test_push_gate_race import _push


def _gating(tmp_path: Path, line: str) -> Path:
    repo = _repo(tmp_path)
    (repo / "tools" / "gate.sh").write_text(
        GATE.replace("exit 0\n", f'git -C "$GOH_CROSS_REPO_ROOT" {line}\nexit 0\n')
    )
    _git(repo, "commit", "-q", "-am", "a gate that writes its repository")
    return repo


def test_a_gate_that_writes_the_repos_config_is_refused_and_named(tmp_path):
    repo = _gating(tmp_path, "config remote.origin.url https://elsewhere.invalid/x.git")
    r = _push(repo, tmp_path, "refs/heads/main")
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert (
        "own .git/config" in out and "remote.origin.url=https://elsewhere.invalid/x.git" in out
    ), out


def test_a_token_in_the_written_url_is_redacted(tmp_path):
    secret = "tok" + "en-value-12345"
    repo = _gating(tmp_path, f"config remote.origin.url https://{secret}@example.invalid/x.git")
    r = _push(repo, tmp_path, "refs/heads/main")
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert secret not in out and "https://***@example.invalid/x.git" in out, out


def test_a_moved_ref_is_named_not_refused(tmp_path):
    repo = _gating(tmp_path, "branch backup/flatten-records")
    r = _push(repo, tmp_path, "refs/heads/main")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "moved refs" in out and "refs/heads/backup/flatten-records" in out, out


def test_a_gate_that_writes_nothing_says_nothing(tmp_path):
    repo = _repo(tmp_path)
    r = _push(repo, tmp_path, "refs/heads/main")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "own .git/config" not in out and "moved refs" not in out, out


def test_a_branch_deleted_during_the_gate_takes_its_tracking_keys_unrefused(tmp_path):
    # Another session deleting a tracked branch while a long gate runs removes its
    # `branch.<name>.*` keys with the ref (a goh land on 2026-10-19 was refused for
    # branch.early-exit-pipe.{remote,merge}). They belong to the ref's lifecycle, so they
    # are named with it, like a moved ref, not refused as a gate's write.
    repo = _gating(tmp_path, "branch -D stale")
    _git(repo, "branch", "stale")
    _git(repo, "config", "branch.stale.remote", "origin")
    _git(repo, "config", "branch.stale.merge", "refs/heads/stale")
    r = _push(repo, tmp_path, "refs/heads/main")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "own .git/config" not in out, out
    assert "refs/heads/stale" in out and "branch.stale.remote" in out, out


def test_a_tracking_key_written_for_a_branch_that_did_not_move_is_refused(tmp_path):
    repo = _gating(tmp_path, "config branch.main.remote elsewhere")
    r = _push(repo, tmp_path, "refs/heads/main")
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "own .git/config" in out and "branch.main.remote=elsewhere" in out, out


def _beside_a_sibling(tmp_path: Path, during: str) -> tuple[Path, Path]:
    """A repo whose gate runs `during` (lines run as the sibling worktree's session would, against
    the SHARED config), with a sibling worktree on `lane-x` that tracks main, as a lane made from
    origin/main does."""
    repo = _repo(tmp_path)
    sib = tmp_path / "sib"
    (repo / "tools" / "gate.sh").write_text(GATE.replace("exit 0\n", f"{during}\nexit 0\n"))
    _git(repo, "commit", "-q", "-am", "a gate beside a sibling lane")
    _git(repo, "worktree", "add", "-q", "-b", "lane-x", str(sib), "main")
    _git(repo, "config", "branch.lane-x.remote", "origin")
    _git(repo, "config", "branch.lane-x.merge", "refs/heads/main")
    return repo, sib


def test_a_sibling_recreating_its_branch_at_the_same_commit_is_not_the_gates_write(tmp_path):
    # GOH.SHAREDCONFIG (2026-11-14): linked worktrees share .git/config. A lane's land was refused
    # for "wrote wt's own .git/config" while the coordinator retired a sibling lane and made it
    # again from main AT THE SAME COMMIT: the ref never moved, so its tracking keys' going was
    # read as this gate's write. A branch checked out in another worktree is that worktree's.
    sib = tmp_path / "sib"
    repo, _ = _beside_a_sibling(
        tmp_path,
        f'git -C "$GOH_CROSS_REPO_ROOT" worktree remove --force "{sib}"\n'
        'git -C "$GOH_CROSS_REPO_ROOT" branch -q -D lane-x\n'
        f'git -C "$GOH_CROSS_REPO_ROOT" worktree add -q -b lane-x "{sib}" main',
    )
    r = _push(repo, tmp_path, "refs/heads/main")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "own .git/config" not in out, out
    assert "branch.lane-x.remote" in out, out  # named, as a moved ref is


def test_a_sibling_setting_its_own_branchs_upstream_is_not_the_gates_write(tmp_path):
    sib = tmp_path / "sib"
    repo, _ = _beside_a_sibling(tmp_path, f'git -C "{sib}" config branch.lane-x.merge refs/heads/x')
    r = _push(repo, tmp_path, "refs/heads/main")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "own .git/config" not in out and "branch.lane-x.merge" in out, out


def test_a_gate_writing_config_beside_a_sibling_is_still_refused(tmp_path):
    # The sibling excuses only ITS branch's keys: the gated branch's, and any other key, are writes.
    repo, _ = _beside_a_sibling(
        tmp_path,
        'git -C "$GOH_CROSS_REPO_ROOT" config branch.main.remote elsewhere\n'
        'git -C "$GOH_CROSS_REPO_ROOT" config branch.gone.remote elsewhere',
    )
    r = _push(repo, tmp_path, "refs/heads/main")
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "own .git/config" in out and "branch.main.remote=elsewhere" in out, out
    assert "branch.gone.remote=elsewhere" in out, out
