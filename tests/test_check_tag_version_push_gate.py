"""check_tag_version.py — wired into the push, through the real pre-push gate.

Split from `test_check_tag_version.py` by concern rather than for length alone: that file is the
checker's own rules (what a tag is compared against, which refs are in scope, which layouts count
as a declaration); this one is the wiring every repo actually runs — `gates/push_gate.sh`, fed the
refs on stdin exactly as git hands them to the hook.

The wiring is where the rules meet the skip list. push_gate skips a commit the remote already has,
and it exits before making a worktree on a refusal; both were places this check could quietly stop
running, so both are pinned here rather than inferred from the checker's unit cases.
"""

import subprocess

from conftest import REPO_ROOT, git, write

from _tag_version_kit import _media_shape, _refs, _sha, origin_from
from _fast_git import fast_init  # noqa: E402


def _push(repo, tmp_path, *pairs, extra_env=None, url="unused-url"):
    """Drive the real pre-push gate with these refs, as git would."""
    import os

    env = dict(
        os.environ,
        GOH_DIR=str(REPO_ROOT),
        GOH_PUSH_LOGS=str(tmp_path / "push-logs"),
        GATE_REPORT=str(tmp_path / "report.txt"),
        **(extra_env or {}),
    )
    return subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "push_gate.sh"), "origin", url],
        cwd=repo,
        env=env,
        text=True,
        input=_refs(*pairs),
        capture_output=True,
    )


def test_the_push_gate_refuses_a_lying_tag(tmp_path):
    """End to end through gates/push_gate.sh, which is what every repo runs."""
    repo = tmp_path / "media"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "tools/gate.sh", "#!/usr/bin/env bash\nexit 0\n")
    _media_shape(repo, "1.79.1")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v1.79.3")

    r = _push(repo, tmp_path, ("refs/tags/v1.79.3", _sha(repo, "v1.79.3")))
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "1.79.1" in out and "1.79.3" in out, out
    assert "nothing pushed" in out, out
    # One line: the repo's own checkout. The throwaway export is gone — a
    # refusal that exits before the worktree is made must still leave none.
    assert len(git(repo, "worktree", "list").strip().splitlines()) == 1, git(
        repo, "worktree", "list"
    )


def test_the_push_gate_checks_a_tag_whose_commit_the_remote_already_has(tmp_path):
    """The retag case, and the reason the check runs before every skip below it.

    push_gate skips a commit the remote already has (ZoneWM: six never-pushed
    tags on days-old commits, each costing a cold gate). That skip is about
    CODE; a tag's version claim is not, and re-tagging an old commit to a new
    version is exactly how a lie gets published.
    """
    repo = tmp_path / "media"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "tools/gate.sh", "#!/usr/bin/env bash\nexit 0\n")
    _media_shape(repo, "1.79.1")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    old = _sha(repo, "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", old)  # remote already has it
    git(repo, "tag", "v1.79.3", old)

    r = _push(repo, tmp_path, ("refs/tags/v1.79.3", _sha(repo, "v1.79.3")))
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "1.79.1" in out, out


def test_the_push_gate_allows_a_truthful_tag_and_still_runs_the_code_gate(tmp_path):
    """Not a veto on everything: a matching tag passes, and the worktree gate
    for its commit still happens."""
    repo = tmp_path / "media"
    repo.mkdir()
    fast_init(repo, "main")
    write(
        repo,
        "tools/gate.sh",
        '#!/usr/bin/env bash\nset -euo pipefail\necho "ran=$PWD" >> "$GATE_REPORT"\n',
    )
    _media_shape(repo, "1.79.3")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v1.79.3")

    r = _push(repo, tmp_path, ("refs/tags/v1.79.3", _sha(repo, "v1.79.3")))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ran=" in (tmp_path / "report.txt").read_text(), "the code gate was skipped"


def test_a_branch_only_push_is_not_a_named_non_run_of_the_tag_check(tmp_path):
    repo = tmp_path / "media"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "tools/gate.sh", "#!/usr/bin/env bash\nexit 0\n")
    _media_shape(repo, "1.0.0")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v9.9.9")  # a stale liar in the repo
    # ...and on the remote, pushed long ago. A local release tag the remote LACKS is its own
    # finding now (test_tag_version_unpushed.py); this case is the version half, which judges
    # only the tags a push names.
    origin_from(repo, tmp_path / "remote.git")

    r = _push(
        repo, tmp_path, ("refs/heads/main", _sha(repo, "HEAD")), url=str(tmp_path / "remote.git")
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not applicable" in r.stdout, r.stdout


def test_the_push_gate_leaves_no_refs_file_behind(tmp_path):
    """The refs capture is a file in TMPDIR, so it can be leaked by every push.

    A private TMPDIR is the seam that makes this measurable: globbing the SHARED
    one measures the other xdist workers too, and a green run would be a
    coincidence. This test passed alone and failed in the full suite for exactly
    that reason, which is what it is here to catch about itself.
    """
    repo = tmp_path / "media"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "tools/gate.sh", "#!/usr/bin/env bash\nexit 0\n")
    _media_shape(repo, "1.0.0")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    for green, refs in (
        (True, [("refs/heads/main", _sha(repo, "HEAD"))]),
        (False, [("refs/tags/v1.79.3", _sha(repo, "HEAD"))]),
    ):
        r = _push(repo, tmp_path, *refs, extra_env={"TMPDIR": str(scratch)})
        assert (r.returncode == 0) is green, r.stdout + r.stderr
        assert not list(scratch.glob("goh-push-refs.*")), (
            f"{'a green' if green else 'a refused'} push leaked its refs file"
        )


def test_a_tag_check_that_cannot_run_is_not_reported_as_a_mismatch(tmp_path):
    """No binary is a refusal -- but of "could not run", not "does not match". The two read
    alike in a push log and send the reader to opposite fixes (the tag, or the build)."""
    repo = tmp_path / "media"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "tools/gate.sh", "#!/usr/bin/env bash\nexit 0\n")
    _media_shape(repo, "1.79.3")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v1.79.3")

    r = _push(
        repo,
        tmp_path,
        ("refs/tags/v1.79.3", _sha(repo, "v1.79.3")),
        extra_env={"GOH_BIN": str(tmp_path / "no-such-goh")},
    )
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "the tag check could not run (exit 2" in out, out
    assert "does not match" not in out, out


def test_the_push_gate_refuses_a_release_tag_follow_tags_left_behind(tmp_path):
    """The servers fleet, 2026-10-10: `git tag v1.10.3` (lightweight), then `git push --follow-tags`
    sent main and not the tag. The real gate is handed the remote's name, so it asks the remote."""
    repo = tmp_path / "fleet"
    repo.mkdir()
    fast_init(repo, "main")
    write(repo, "tools/gate.sh", "#!/usr/bin/env bash\nexit 0\n")
    write(repo, "VERSION", "1.10.2\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "v1.10.2")
    origin_from(repo, tmp_path / "remote.git")
    write(repo, "VERSION", "1.10.3\n")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "release: v1.10.3")
    git(repo, "tag", "v1.10.3")
    new = _sha(repo, "HEAD")

    r = _push(repo, tmp_path, ("refs/heads/main", new), url=str(tmp_path / "remote.git"))
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "git push origin v1.10.3" in out and "nothing pushed" in out, out
