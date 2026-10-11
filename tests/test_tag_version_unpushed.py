"""A release tag that names a commit the remote has must be ON the remote (`goh tag-version --remote`).

The incident (servers fleet, 2026-10-10): `storage-server` and `adguard_server` were released by hand
-- commit VERSION, `git tag v1.10.3` (LIGHTWEIGHT), `git push --follow-tags`. `--follow-tags` sends
annotated tags only, so main moved and the tag stayed local; both hosts then described themselves as
`v1.10.2-2-gd9420ac` and failed the fleet's H4 until the tags were pushed by name. A survey the same
day found the class twice more, older: `antiknob v0.13.0` and `divoom-control v0.35.0`, annotated
release tags on commits origin/main has held for a month, never pushed.

So the rule is about the TAG, not the bump: a local `v<semver>` tag reachable from a branch being
pushed must be in the push or already on the remote. A version bump with no tag yet is fine --
monitor and antiknob push the bump first and tag after CI, by design.
"""

import pytest

from conftest import git, write
from tier_kit import both_tiers, run_tiered  # noqa: F401  # both_tiers: a fixture

from _tag_version_kit import ZERO, _sha, origin_from
from _fast_git import fast_init  # noqa: E402

pytestmark = pytest.mark.usefixtures("both_tiers")

NO_HOOKS = ("-c", "core.hooksPath=/dev/null", "-c", "user.name=t", "-c", "user.email=t@t")


def _g(repo, *args):
    return git(repo, *NO_HOOKS, *args)


def _commit(repo, version):
    write(repo, "VERSION", f"{version}\n")
    _g(repo, "add", "-A")
    _g(repo, "commit", "-qm", f"release: v{version}")
    return _sha(repo, "HEAD")


def _released(tmp_path):
    """v1.0.0 committed, tagged and on origin with its tag; v1.0.1 committed and not yet pushed."""
    repo = tmp_path / "repo"
    fast_init(repo, "main")
    first = _commit(repo, "1.0.0")
    _g(repo, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    origin_from(repo, tmp_path / "remote.git")
    second = _commit(repo, "1.0.1")
    return repo, first, second


def _check(repo, tmp_path, lines, *extra):
    refs = tmp_path / "refs.txt"
    refs.write_text("".join(f"{line}\n" for line in lines))
    return run_tiered(
        repo, "goh", "tag-version", "--refs-file", str(refs), "--remote", "origin", *extra
    )


def _main(new, old):
    return f"refs/heads/main {new} refs/heads/main {old}"


def test_the_incident_a_lightweight_tag_left_behind_by_follow_tags_is_red(tmp_path):
    repo, first, second = _released(tmp_path)
    _g(repo, "tag", "v1.0.1")  # lightweight, as the fleet repos were tagged
    r = _check(repo, tmp_path, [_main(second, first)])
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "refs/tags/v1.0.1" in out and second[:12] in out, out
    assert "lightweight" in out and "--follow-tags" in out, out
    assert "git push origin v1.0.1" in out, out


def test_the_tag_sent_in_the_same_push_is_green(tmp_path):
    repo, first, second = _released(tmp_path)
    _g(repo, "tag", "v1.0.1")
    r = _check(
        repo, tmp_path, [_main(second, first), f"refs/tags/v1.0.1 {second} refs/tags/v1.0.1 {ZERO}"]
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_tag_the_remote_already_has_is_green(tmp_path):
    repo, first, second = _released(tmp_path)
    _g(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")
    _g(repo, "push", "-q", "origin", "v1.0.1")
    r = _check(repo, tmp_path, [_main(second, first)])
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_bump_pushed_before_its_tag_exists_is_green(tmp_path):
    """monitor's and antiknob's release flow: the bump reaches main, CI runs, then the tag."""
    repo, first, second = _released(tmp_path)
    r = _check(repo, tmp_path, [_main(second, first)])
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_old_release_tag_the_remote_never_got_is_red_on_the_next_push(tmp_path):
    """antiknob v0.13.0's shape: annotated, on a commit the remote has held for weeks."""
    repo, first, second = _released(tmp_path)
    _g(repo, "push", "-q", "origin", "main")
    _g(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")  # tagged after main went up, never pushed
    third = _commit(repo, "1.0.2")
    r = _check(repo, tmp_path, [_main(third, second)])
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "refs/tags/v1.0.1" in out and "annotated" in out, out
    assert "git push origin v1.0.1" in out, out


def test_a_remote_that_cannot_be_asked_refuses_rather_than_passes(tmp_path):
    repo, first, second = _released(tmp_path)
    _g(repo, "tag", "v1.0.1")
    _g(repo, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    r = _check(repo, tmp_path, [_main(second, first)])
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "cannot ask origin" in out, out


def test_tags_that_are_not_release_tags_or_not_reachable_are_not_judged(tmp_path):
    repo, first, second = _released(tmp_path)
    _g(repo, "tag", "nightly")
    _g(repo, "tag", "v1.0")  # not v<semver>
    _g(repo, "checkout", "-q", "-b", "side", first)
    _commit(repo, "9.9.9")
    _g(repo, "tag", "v9.9.9")  # a release tag, but not on the branch being pushed
    _g(repo, "checkout", "-q", "main")
    r = _check(repo, tmp_path, [_main(second, first)])
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_branch_delete_carries_no_commit_to_judge(tmp_path):
    repo, first, _second = _released(tmp_path)
    _g(repo, "tag", "v1.0.1")
    r = _check(repo, tmp_path, [f"(delete) {ZERO} refs/heads/old {first}"])
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_ok_line_says_what_the_remote_half_judged(tmp_path):
    """A pass names the tags it compared, so a pass over nothing cannot read as a pass."""
    repo, first, second = _released(tmp_path)
    r = _check(repo, tmp_path, [_main(second, first)])
    assert r.returncode == 0, r.stdout + r.stderr
    assert (
        "1 local release tag(s) reachable from the pushed branch(es) are on origin" in r.stdout
    ), r.stdout


def test_a_pinned_sha_push_is_judged_by_the_branch_it_moves(tmp_path):
    """ZoneWM's `make push` is `git push v2origin <sha>:refs/heads/main`: the hook's local ref is
    the sha itself, so the branch being moved is read from the REMOTE ref."""
    repo, first, second = _released(tmp_path)
    _g(repo, "tag", "v1.0.1")
    r = _check(repo, tmp_path, [f"{second} {second} refs/heads/main {first}"])
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "git push origin v1.0.1" in out, out


def test_the_remote_is_asked_at_its_url_and_named_by_its_name(tmp_path):
    """The hook gets the remote's name ($1) and URL ($2); gate_profile.sh pushes to a made-up name
    whose URL is the repo itself, so asking by name would refuse every profile."""
    repo, first, second = _released(tmp_path)
    _g(repo, "tag", "v1.0.1")
    refs = tmp_path / "refs.txt"
    refs.write_text(_main(second, first) + "\n")

    def ask(url):
        return run_tiered(
            repo, "goh", "tag-version", "--refs-file", str(refs),
            "--remote", "nowhere", "--remote-url", url,
        )  # fmt: skip

    to_origin = ask(str(tmp_path / "remote.git"))
    assert to_origin.returncode == 1, to_origin.stdout + to_origin.stderr
    assert "git push nowhere v1.0.1" in to_origin.stderr, to_origin.stderr
    to_itself = ask(str(repo))
    assert to_itself.returncode == 0, to_itself.stdout + to_itself.stderr
