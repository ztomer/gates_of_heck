"""A gate run never writes the repository it gates (BACKLOG 6.11).

The games metarepo's `origin` read `https://elsewhere.invalid/x.git` from 2026-10-08 until it was
repaired by hand: a probe in that commit's own hook run wrote it into the REAL `.git/config`, and
nothing compared the config before and after. The push gate now snapshots the repository's config
and refs before the gate and reads them again after it (lib/repo_writes.sh): a config write refuses
the push naming the key, a moved ref is named and not refused (another session commits during a long
gate), and a token in a URL is redacted from the report.

Two pushes go through the gate, one each way; the rules are lib/repo_writes.sh's own two functions,
asked as the gate asks them (`_writes`, GOH.PYSLOW2).
"""

import subprocess
from pathlib import Path

from conftest import REPO_ROOT
from test_push_gate import GATE, _git, _repo
from test_push_gate_race import _push

SECRET = "tok" + "en-value-12345"


def _writes(repo: Path, during: str) -> tuple[bool, str]:
    """(let through, what was said) when DURING runs in REPO between the snapshot and the report."""
    prog = (
        'set -eu; GOH="$1"; cd "$2"; . "$GOH/tui/lib.sh"; . "$GOH/lib/repo_writes.sh"\n'
        'before="$(goh_repo_snapshot "$2")"\n'
        f"{{ {during}; }} >/dev/null 2>&1\n"
        'goh_repo_writes "$2" "$before"'
    )
    r = subprocess.run(["bash", "-c", prog, "writes", str(REPO_ROOT), str(repo)], text=True,
                       capture_output=True, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    return r.returncode == 0, r.stdout + r.stderr


def _gating(tmp_path: Path, *lines: str) -> Path:
    repo = _repo(tmp_path)
    git = 'git -C "$GOH_CROSS_REPO_ROOT"'
    (repo / "tools" / "gate.sh").write_text(
        GATE.replace("exit 0\n", "".join(f"{git} {line}\n" for line in lines) + "exit 0\n")
    )
    _git(repo, "commit", "-q", "-am", "a gate that writes its repository")
    return repo


def test_a_gate_that_writes_the_repos_config_is_refused_and_named(tmp_path):
    repo = _gating(tmp_path, f"config remote.origin.url https://{SECRET}@example.invalid/x.git")
    r = _push(repo, tmp_path, "refs/heads/main")
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "nothing pushed" in out, out
    assert "own .git/config" in out, out
    # ... and a token in the written URL is redacted.
    assert SECRET not in out, out
    assert "remote.origin.url=https://***@example.invalid/x.git" in out, out


def test_a_moved_ref_is_named_not_refused_and_takes_its_tracking_keys(tmp_path):
    """Another session deleting a tracked branch while a long gate runs removes its
    `branch.<name>.*` keys with the ref (a goh land on 2026-10-19 was refused for
    branch.early-exit-pipe.{remote,merge}). They belong to the ref's lifecycle, so they are named
    with it, like a moved ref, not refused as a gate's write."""
    repo = _gating(tmp_path, "branch backup/flatten-records", "branch -D stale")
    _git(repo, "branch", "stale")
    _git(repo, "config", "branch.stale.remote", "origin")
    _git(repo, "config", "branch.stale.merge", "refs/heads/stale")
    r = _push(repo, tmp_path, "refs/heads/main")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "moved refs" in out and "refs/heads/backup/flatten-records" in out, out
    assert "own .git/config" not in out, out
    assert "refs/heads/stale" in out and "branch.stale.remote" in out, out


def test_a_gate_that_writes_nothing_says_nothing(tmp_path):
    through, out = _writes(_repo(tmp_path), "true")
    assert through and "own .git/config" not in out and "moved refs" not in out, out


def test_a_tracking_key_written_for_a_branch_that_did_not_move_is_refused(tmp_path):
    through, out = _writes(_repo(tmp_path), "git config branch.main.remote elsewhere")
    assert not through, out
    assert "own .git/config" in out and "branch.main.remote=elsewhere" in out, out


def _beside_a_sibling(tmp_path: Path) -> tuple[Path, Path]:
    """A repo with a sibling worktree on `lane-x` that tracks main, as a lane made from origin/main
    does: what runs in it writes the SHARED config."""
    repo, sib = _repo(tmp_path), tmp_path / "sib"
    _git(repo, "worktree", "add", "-q", "-b", "lane-x", str(sib), "main")
    _git(repo, "config", "branch.lane-x.remote", "origin")
    _git(repo, "config", "branch.lane-x.merge", "refs/heads/main")
    return repo, sib


def test_a_sibling_worktrees_branch_keys_are_its_own_and_no_other_key_is(tmp_path):
    repo, sib = _beside_a_sibling(tmp_path)
    # The sibling excuses only ITS branch's keys: the gated branch's, and any other key, are writes.
    through, out = _writes(
        repo,
        "git config branch.main.remote elsewhere; git config branch.gone.remote elsewhere",
    )
    assert not through, out
    assert "own .git/config" in out and "branch.main.remote=elsewhere" in out, out
    assert "branch.gone.remote=elsewhere" in out, out

    through, out = _writes(repo, f"git -C '{sib}' config branch.lane-x.merge refs/heads/x")
    assert through, out
    assert "own .git/config" not in out and "branch.lane-x.merge" in out, out

    # GOH.SHAREDCONFIG (2026-11-14): linked worktrees share .git/config. A lane's land was refused
    # for "wrote wt's own .git/config" while the coordinator retired a sibling lane and made it
    # again from main AT THE SAME COMMIT: the ref never moved, so its tracking keys' going was
    # read as this gate's write. A branch checked out in another worktree is that worktree's.
    through, out = _writes(
        repo,
        f"git worktree remove --force '{sib}'; git branch -q -D lane-x; "
        f"git worktree add -q -b lane-x '{sib}' main",
    )
    assert through, out
    assert "own .git/config" not in out, out
    assert "branch.lane-x.remote" in out, out  # named, as a moved ref is
