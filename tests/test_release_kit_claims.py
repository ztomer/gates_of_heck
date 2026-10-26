"""release.sh claims only what it verified (G15).

A `git push` that returns 0 over a remote that did not move (a hook-less proxy, a server-side
hook that accepts and discards, a wrapper) printed "pushed" and went on to create a GitHub release
for a tag origin does not have. The kit reads origin back before it says so, as tap_bump.sh already
does for the tap. Likewise `gh release create`: the release is looked up afterwards.
"""

from __future__ import annotations

from pathlib import Path

from test_release_kit import TAG, kit, run_release  # noqa: F401 (kit is a fixture)

LYING_PUSH = """\
#!/usr/bin/env bash
printf 'git %s\\n' "$*" >> "${GOHKIT_GIT_LOG:?}"
[ "$1" = push ] && exit 0
exec /usr/bin/git "$@"
"""


def test_a_push_that_returned_0_over_an_unmoved_origin_is_not_pushed(kit: dict) -> None:  # noqa: F811
    (kit["bin"] / "git").write_text(LYING_PUSH)
    r = run_release(kit)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "pushed" not in r.stdout.replace("not pushed", ""), r.stdout
    assert TAG in r.stderr and "origin" in r.stderr, r.stderr
    assert not kit["gh_log"].exists() or "release create" not in kit["gh_log"].read_text()


def test_a_release_gh_created_but_cannot_find_is_not_created(kit: dict, tmp_path: Path) -> None:  # noqa: F811
    gh = kit["bin"] / "gh"
    gh.write_text(gh.read_text().replace(': > "$STATE/rel-$tag"', ":"))
    r = run_release(kit)
    assert r.returncode != 0 and "created" not in r.stdout.replace("not created", ""), r.stdout
    assert "cannot be found" in r.stderr, r.stderr
