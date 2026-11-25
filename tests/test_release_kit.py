"""tools/release-kit — release.sh, gen_app_icons.py, update_dev.sh.

release.sh runs its steps in order (gate → changelog → tag → push →
release → tap); every failure names its step; tagging and gh-release are
idempotent; --dry-run performs NOTHING; a missing stanza fails the changelog
step. gen_app_icons.py emits exactly the ten Apple ladder members plus an
.icns (sizes verified from PNG IHDR bytes). update_dev.sh replaces the
installed copy, strips quarantine (proven), signs, launches only on request.

git is REAL throughout (local bare remotes, so push semantics are genuine).
Only gh is faked (stateful stub: `release view` succeeds iff previously
created).

This file is the release FLOW alone; the other two tools have their own files, by concern rather
than for length alone: `test_release_kit_gen_app_icons.py` (the icon ladder, verified from PNG
bytes) and `test_release_kit_update_dev.py` (the dev-machine install). Neither shares a fixture
with a real-release run, so neither carries the xdist group below.
"""

import hashlib
import subprocess
from pathlib import Path

import pytest
from _tree_clone import clone_tree, template

# Same xdist group as test_release_hardening.py: its mid-run-edit test corrupts release.sh on purpose.
from conftest import FAKE_GH, REPO_ROOT, hermetic_env, native_goh_path

RELEASE = REPO_ROOT / "tools" / "release-kit" / "release.sh"

GITHUB_ORIGIN = "https://github.com/t/proj.git"
VERSION = "1.2.3"
TAG = f"v{VERSION}"
STANZA_BODY = "- Added the release kit\n- Fixed seven drifting releasers"

# ── helpers ──────────────────────────────────────────────────────────────────


def sh(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    return r.stdout


def commit_all(repo: Path, msg: str = "fixture") -> None:
    sh(repo, "add", "-A")
    sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", msg)


def add_github_origin(proj: Path, bare: Path) -> None:
    """origin as a GitHub URL, as a released repo's is, which git sends to the local `bare`: the kit
    reads the slug off the URL, so a local path as origin is no GitHub at all."""
    sh(proj, "remote", "add", "origin", GITHUB_ORIGIN)
    sh(proj, "config", f"url.{bare}.insteadOf", GITHUB_ORIGIN)


GIT_WRAPPER = """\
#!/bin/sh
printf 'git %s\\n' "$*" >> "${GOHKIT_GIT_LOG:?}"
exec /usr/bin/git "$@"
"""


KIT_PARTS = ("proj", "origin.git", "bin", "gh-state")


def _build_kit(root: Path) -> None:
    """The kit's parts, all but the origin's `insteadOf` (an absolute path, set per clone)."""
    proj = root / "proj"
    proj.mkdir(parents=True)
    sh(proj, "init", "-q", "-b", "main")
    (proj / "CHANGELOG.md").write_text(
        f"# CHANGELOG\n\n## {TAG}\n\n{STANZA_BODY}\n\n## v1.1.0\n\n- older\n",
        encoding="utf-8",
    )
    # The commit being tagged declares its version, as the tag check requires (GOH.REL2).
    (proj / "VERSION").write_text(f"{VERSION}\n", encoding="utf-8")
    commit_all(proj)
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(proj), str(root / "origin.git")],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    sh(root / "origin.git", "remote", "remove", "origin")  # it named the template's proj
    sh(proj, "remote", "add", "origin", GITHUB_ORIGIN)
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (root / "gh-state").mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    (bin_dir / "git").write_text(GIT_WRAPPER)
    (bin_dir / "git").chmod(0o755)


@pytest.fixture
def kit(tmp_path: Path, tmp_path_factory) -> dict:
    """A real project repo with a bare origin, stanza'd CHANGELOG, fake gh: built once per worker,
    each part cloned per test (GOH.FIXTURECOST)."""
    built = template(tmp_path_factory, "release-kit", _build_kit)
    for part in KIT_PARTS:
        clone_tree(built / part, tmp_path / part)
    proj, origin = tmp_path / "proj", tmp_path / "origin.git"
    sh(proj, "config", f"url.{origin}.insteadOf", GITHUB_ORIGIN)
    return {
        "proj": proj,
        "origin": origin,
        "bin": tmp_path / "bin",
        "gh_state": tmp_path / "gh-state",
        "gh_log": tmp_path / "gh.log",
        "git_log": tmp_path / "git.log",
    }


def run_release(
    kit: dict, *args: str, gate: str | None = "true", env_extra: dict | None = None
) -> subprocess.CompletedProcess:
    env = hermetic_env()  # no caller GOH_* (GOH.LIVEROOTLEAK); GOH_DIR is this checkout
    env["PATH"] = f"{kit['bin']}:{env['PATH']}"
    env["GH_STATE"] = str(kit["gh_state"])
    env["GH_LOG"] = str(kit["gh_log"])
    env["GOHKIT_GIT_LOG"] = str(kit["git_log"])
    # The suite's goh, named: else every release stamps this checkout's sources (25 git calls).
    env["GOH_BIN"] = str(native_goh_path())
    env.update(
        {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
    )
    for k in ("GOH_RELEASE_GATE", "GOH_RELEASE_BUFFERED"):
        env.pop(k, None)
    env.update(env_extra or {})
    return subprocess.run(
        [
            "/bin/bash",
            str(RELEASE),
            "--version",
            VERSION,
            *(["--gate", gate] if gate else []),
            *args,
        ],
        cwd=kit["proj"],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
    )


# ── step sequencing ──────────────────────────────────────────────────────────


class TestSequencing:
    def test_happy_path_runs_every_step_in_order_and_a_second_run_does_none_again(self, kit):
        r = run_release(kit)
        assert r.returncode == 0, r.stdout + r.stderr

        # changelog step passed → tag exists as an ANNOTATED tag
        assert sh(kit["proj"], "cat-file", "-t", TAG).strip() == "tag"

        # gh was called to create the release with the stanza body as notes
        log = kit["gh_log"].read_text()
        assert f"gh release view {TAG}" in log
        assert log.count(f"gh release create {TAG}") == 1
        notes = (kit["gh_state"] / f"notes-{TAG}").read_text()
        assert STANZA_BODY.splitlines()[0] in notes

        # the branch and the tag are on origin, in ONE push: two left a window where the branch
        # carried a release tag the remote lacked, and the pre-push gate refuses exactly that
        # (`goh tag-version --remote`). One push is one gate run, and a refused one leaves neither.
        first = sh(kit["proj"], "rev-parse", TAG).strip()
        head = sh(kit["proj"], "rev-parse", "HEAD").strip()
        for ref, sha in ((f"refs/tags/{TAG}", first), ("refs/heads/main", head)):
            assert sh(kit["origin"], "rev-parse", "--verify", ref).strip() == sha
        pushes = [c for c in kit["git_log"].read_text().splitlines() if c.startswith("git push")]
        assert pushes == [f"git push origin main {TAG}"], pushes

        # the second run, the same release: neither the tag nor the GitHub release is made again
        kit["git_log"].write_text("")
        r = run_release(kit)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "git tag -a" not in kit["git_log"].read_text()
        assert "already exists" in r.stdout
        assert sh(kit["proj"], "rev-parse", TAG).strip() == first
        creates = kit["gh_log"].read_text().count(f"gh release create {TAG}")
        assert creates == 1, "second run re-created the GitHub release"

    def test_gate_failure_blocks_everything_and_names_the_step(self, kit):
        r = run_release(kit, "--gate", "false", "--no-push")
        assert r.returncode != 0
        combined = r.stdout + r.stderr
        assert "'gate'" in combined, combined
        assert not (kit["proj"] / ".git" / "refs" / "tags" / TAG).exists()
        assert not kit["gh_log"].exists() or "create" not in kit["gh_log"].read_text()

    def test_the_archive_build_and_the_verify_command_block_the_tag(self, kit):
        """The tarball a consumer builds from has no gitignored files. A build that needs one
        passes locally and must fail HERE, before the tag; so must a verify command that does."""
        proj = kit["proj"]
        (proj / ".gitignore").write_text("vendor/\n")
        (proj / "vendor").mkdir()
        (proj / "vendor" / "dep.txt").write_text("needed at build time\n")
        commit_all(proj)  # .gitignore tracked; vendor/dep.txt is not
        r = run_release(kit, "--no-push", "--archive-build", "test -f vendor/dep.txt")
        assert r.returncode != 0
        assert "'archive'" in r.stdout + r.stderr
        assert not (proj / ".git" / "refs" / "tags" / TAG).exists()
        # Track it and the same build passes; the gate ran in the WORKTREE and
        # the archive step in a fresh extraction, so both cannot be the same dir.
        sh(proj, "add", "-f", "vendor/dep.txt")
        commit_all(proj)
        args = ("--no-push", "--archive-build",
                'test -f vendor/dep.txt && test "$PWD" != "$OLDPWD"', "--verify")  # fmt: skip
        r = run_release(kit, *args, 'test "$GOH_RELEASE_VERSION" = 9.9.9')
        assert r.returncode != 0
        assert "'verify'" in r.stdout + r.stderr
        assert not (proj / ".git" / "refs" / "tags" / TAG).exists()
        r = run_release(kit, *args, f'test "$GOH_RELEASE_VERSION" = {VERSION}')
        assert r.returncode == 0, r.stdout + r.stderr
        assert sh(proj, "cat-file", "-t", TAG).strip() == "tag"
        # --no-push: origin untouched, and no release of a tag it does not have
        assert TAG not in sh(kit["origin"], "tag", "-l")
        assert "--no-push implies no GitHub release" in r.stderr, r.stderr

    def test_missing_changelog_stanza_fails_naming_the_step(self, kit):
        (kit["proj"] / "CHANGELOG.md").write_text("## v9.9.9\n\n- other\n")
        commit_all(kit["proj"])
        r = run_release(kit, "--no-push")
        assert r.returncode != 0
        combined = r.stdout + r.stderr
        assert "'changelog'" in combined
        assert VERSION in combined
        # the failure happened BEFORE any side effect
        assert not (kit["proj"] / ".git" / "refs" / "tags" / TAG).exists()
        assert not kit["gh_log"].exists() or "create" not in kit["gh_log"].read_text()


# ── dry run ──────────────────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_has_zero_side_effects(self, kit):
        marker = kit["proj"] / "gate-marker"
        (kit["proj"] / "unpushed").write_text("")  # so a push of the branch would be seen
        commit_all(kit["proj"])
        before = sh(kit["origin"], "for-each-ref")
        r = run_release(
            kit,
            "--dry-run",
            "--gate",
            f"touch {marker}",
            "--tap",
            "/nonexistent-tap",
            "--cask",
            "foo",
            "--artifact",
            "/nonexistent.tar.gz",
        )
        assert r.returncode == 0, r.stdout + r.stderr
        # The self-buffered copy runs from $TMPDIR, where `../../gates/_from_head.sh` is nothing:
        # every release since 4cc8976 printed "No such file" and "command not found" first.
        for sign in ("No such file", "command not found", "syntax error"):
            assert sign not in r.stderr, r.stderr

        assert "[dry-run]" in r.stdout
        assert "(repo t/proj)" in r.stdout, r.stdout  # the slug, read through `insteadOf`
        assert not marker.exists(), "dry-run executed the gate"
        out = sh(kit["proj"], "tag", "-l")
        assert TAG not in out, "dry-run created the tag"
        assert sh(kit["origin"], "for-each-ref") == before, "dry-run pushed"
        assert not kit["gh_log"].exists() or "create" not in kit["gh_log"].read_text()


# ── backfill: earlier tags on origin that never got a release ───────────────
# Tags cut by hand (bypassing this kit) stayed release-less forever and silently:
# ztools had v3.4.0, v3.4.1 and v3.5.0 on origin with v3.3.0 still "Latest". The
# release step closes that class for every earlier tag, not just the one cut now.

EARLIER_STANZAS = "## v1.1.0\n\n- the hand-made one\n\n## v1.0.5\n\n- already released\n"


@pytest.fixture
def hand_tags(kit: dict) -> dict:
    """origin carries earlier tags made by hand: v1.1.0 (stanza, no release),
    v1.0.5 (stanza, release exists), v1.0.0 (no stanza, no release) and a
    pre-release v1.1.0-rc1 that is not a version tag of the kit's scheme."""
    proj = kit["proj"]
    (proj / "CHANGELOG.md").write_text(
        f"# CHANGELOG\n\n## {TAG}\n\n{STANZA_BODY}\n\n{EARLIER_STANZAS}", encoding="utf-8"
    )
    commit_all(proj)
    for t in ("v1.0.0", "v1.0.5", "v1.1.0", "v1.1.0-rc1"):
        sh(proj, "tag", t)
    sh(proj, "push", "-q", "origin", "--tags")
    (kit["gh_state"] / "rel-v1.0.5").write_text("")
    return kit


def gh_creates(kit: dict) -> list[str]:
    log = kit["gh_log"]
    lines = log.read_text().splitlines() if log.exists() else []
    return [line for line in lines if line.startswith("gh release create ")]


class TestBackfill:
    def test_hand_made_earlier_tags_are_released_from_their_stanzas_before_the_current(
        self, hand_tags
    ):
        # The dry run plans each backfill, creates nothing and reaches no network.
        r = run_release(hand_tags, "--dry-run")
        assert r.returncode == 0, r.stdout + r.stderr
        plans = [
            line for line in r.stdout.splitlines() if "[dry-run]" in line and "v1.1.0 " in line
        ]
        assert plans and "--latest=false" in plans[0], r.stdout
        assert gh_creates(hand_tags) == []
        log = hand_tags["gh_log"]
        assert not log.exists() or "release list" not in log.read_text(), (
            "dry-run touched the network"
        )
        assert "ls-remote" not in hand_tags["git_log"].read_text(), "dry-run touched the network"

        r = run_release(hand_tags)
        assert r.returncode == 0, r.stdout + r.stderr
        creates = gh_creates(hand_tags)
        # v1.0.5 has its release and is left alone; v1.0.0 has no stanza; the rc is no version tag
        assert [c.split()[3] for c in creates] == ["v1.1.0", TAG], creates
        assert "--latest=false" in creates[0], "a backfill must not steal Latest"
        assert "--latest=false" not in creates[1], "the release being cut stays Latest"
        notes = (hand_tags["gh_state"] / "notes-v1.1.0").read_text()
        assert "the hand-made one" in notes
        assert "already released" not in notes and STANZA_BODY.splitlines()[0] not in notes
        out = r.stdout + r.stderr
        warned = [line for line in out.splitlines() if "v1.0.0" in line]
        assert warned and "stanza" in warned[0] and "⚠" in warned[0], out
        assert "v1.1.0-rc1" not in out, "a pre-release tag is not a version of the kit's scheme"

        # idempotent: a second run finds the backfilled release and leaves it
        assert run_release(hand_tags).returncode == 0
        assert [c.split()[3] for c in gh_creates(hand_tags)] == ["v1.1.0", TAG]


# ── tap bump ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tap(tmp_path: Path) -> tuple[Path, Path]:
    """A bare tap repo seeded with a cask pinned at v0.0.0 (+ its seed clone)."""
    seed = tmp_path / "tap-seed"
    seed.mkdir()
    sh(seed, "init", "-q", "-b", "main")
    cask_dir = seed / "Casks"
    cask_dir.mkdir()
    (cask_dir / "foo.rb").write_text(
        'cask "foo" do\n'
        '  url "https://example.com/foo/archive/refs/tags/v0.0.0.tar.gz"\n'
        '  sha256 "0000000000000000000000000000000000000000000000000000000000000000"\n'
        "end\n",
        encoding="utf-8",
    )
    commit_all(seed)
    bare = tmp_path / "tap.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True, stdin=subprocess.DEVNULL
    )
    return seed, bare


def bare_show(bare: Path, ref: str, rel: str) -> str:
    """Read a file straight out of the bare tap repo (authoritative, no clone)."""
    return subprocess.run(
        ["git", "-C", str(bare), "show", f"{ref}:{rel}"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout


def bare_head(bare: Path) -> str:
    return sh(bare, "rev-parse", "--verify", "refs/heads/main").strip()


class TestTap:
    def test_bump_rewrites_url_and_sha_pushes_and_is_not_recommitted(self, kit, tap):
        _, bare = tap
        artifact = kit["proj"] / "artifact.tar.gz"
        artifact.write_bytes(b"payload-bytes")
        args = ("--tap", str(bare), "--cask", "foo", "--artifact", str(artifact))

        r = run_release(kit, *args)
        assert r.returncode == 0, r.stdout + r.stderr

        want_sha = hashlib.sha256(b"payload-bytes").hexdigest()
        content = bare_show(bare, "main", "Casks/foo.rb")
        assert f"/tags/{TAG}.tar.gz" in content
        assert f'sha256 "{want_sha}"' in content
        assert "v0.0.0" not in content

        head_before = bare_head(bare)
        r = run_release(kit, *args)  # the same formula again
        assert r.returncode == 0, r.stdout + r.stderr
        assert "nothing to commit" in r.stdout
        assert bare_head(bare) == head_before


# ── the GitHub slug ──────────────────────────────────────────────────────────
# A sed that printed only when it stripped `.git` read a GitHub origin without it as no GitHub (no
# CI check, no release), and any other URL ending in `.git` -- this kit's own local origin -- as one.


SLUGS = {
    "https://github.com/o/r.git": "o/r",
    "https://github.com/o/r": "o/r",
    "git@github.com:o/r": "o/r",
    "https://gitlab.com/o/r.git": "",
    "/srv/git/r.git": "",
}


def test_the_slug_is_read_from_any_github_origin_and_no_other():
    """One shell reads them all; that release.sh asks it, `insteadOf` and all, is the dry run's."""
    prog = f'. "{RELEASE.parent / "gh_release.sh"}"; for u; do echo "$(github_slug "$u")"; done'
    got = subprocess.run(["bash", "-c", prog, "slug", *SLUGS], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, check=True)  # fmt: skip
    assert got.stdout.split("\n")[:-1] == list(SLUGS.values()), got.stdout + got.stderr


def test_an_origin_that_is_no_github_is_said_and_gets_no_release(kit):
    sh(kit["proj"], "remote", "set-url", "origin", "/srv/git/r.git")
    r = run_release(kit, "--dry-run")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "is not a github.com remote" in out and "(repo " not in out, out
