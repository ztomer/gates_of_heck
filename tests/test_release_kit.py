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

import os
import subprocess
from pathlib import Path

import pytest

# Same xdist group as test_release_hardening.py: its mid-run-edit test corrupts release.sh on purpose.

from conftest import FAKE_GH, REPO_ROOT

RELEASE = REPO_ROOT / "tools" / "release-kit" / "release.sh"

VERSION = "1.2.3"
TAG = f"v{VERSION}"
STANZA_BODY = "- Added the release kit\n- Fixed seven drifting releasers"

# ── helpers ──────────────────────────────────────────────────────────────────


def sh(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return r.stdout


def commit_all(repo: Path, msg: str = "fixture") -> None:
    sh(repo, "add", "-A")
    sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", msg)


GIT_WRAPPER = """\
#!/usr/bin/env bash
printf 'git %s\\n' "$*" >> "${GOHKIT_GIT_LOG:?}"
exec /usr/bin/git "$@"
"""


@pytest.fixture
def kit(tmp_path: Path) -> dict:
    """A real project repo with a bare origin, stanza'd CHANGELOG, fake gh."""
    proj = tmp_path / "proj"
    proj.mkdir()
    sh(proj, "init", "-q", "-b", "main")
    (proj / "CHANGELOG.md").write_text(
        f"# CHANGELOG\n\n## {TAG}\n\n{STANZA_BODY}\n\n## v1.1.0\n\n- older\n",
        encoding="utf-8",
    )
    commit_all(proj)

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(proj), str(origin)], check=True)
    sh(proj, "remote", "add", "origin", str(origin))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_state = tmp_path / "gh-state"
    gh_state.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    (bin_dir / "git").write_text(GIT_WRAPPER)
    (bin_dir / "git").chmod(0o755)

    return {
        "proj": proj,
        "origin": origin,
        "bin": bin_dir,
        "gh_state": gh_state,
        "gh_log": tmp_path / "gh.log",
        "git_log": tmp_path / "git.log",
    }


def run_release(kit: dict, *args: str, gate: str = "true") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GOH_DIR"] = str(REPO_ROOT)
    env["PATH"] = f"{kit['bin']}:{env['PATH']}"
    env["GH_STATE"] = str(kit["gh_state"])
    env["GH_LOG"] = str(kit["gh_log"])
    env["GOHKIT_GIT_LOG"] = str(kit["git_log"])
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
    return subprocess.run(
        ["/bin/bash", str(RELEASE), "--version", VERSION, "--gate", gate, *args],
        cwd=kit["proj"],
        capture_output=True,
        text=True,
        env=env,
    )


# ── step sequencing ──────────────────────────────────────────────────────────


class TestSequencing:
    def test_happy_path_runs_every_step_in_order(self, kit):
        r = run_release(kit)  # push asserted in its own test below
        assert r.returncode == 0, r.stdout + r.stderr

        # changelog step passed → tag exists as an ANNOTATED tag
        assert sh(kit["proj"], "cat-file", "-t", TAG).strip() == "tag"

        # gh was called to create the release with the stanza body as notes
        log = kit["gh_log"].read_text()
        assert f"gh release view {TAG}" in log
        assert f"gh release create {TAG}" in log
        notes = (kit["gh_state"] / f"notes-{TAG}").read_text()
        assert STANZA_BODY.splitlines()[0] in notes

    def test_gate_failure_blocks_everything_and_names_the_step(self, kit):
        r = run_release(kit, "--gate", "false", "--no-push")
        assert r.returncode != 0
        combined = r.stdout + r.stderr
        assert "'gate'" in combined, combined
        assert not (kit["proj"] / ".git" / "refs" / "tags" / TAG).exists()
        assert not kit["gh_log"].exists() or "create" not in kit["gh_log"].read_text()

    def test_archive_build_sees_tracked_files_only(self, kit):
        """The tarball a consumer builds from has no gitignored files. A build
        that needs one passes locally and must fail HERE, before the tag."""
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
        r = run_release(
            kit,
            "--no-push",
            "--archive-build",
            'test -f vendor/dep.txt && test "$PWD" != "$OLDPWD"',
        )
        assert r.returncode == 0, r.stdout + r.stderr

    def test_verify_runs_with_the_version_and_blocks_the_tag(self, kit):
        proj = kit["proj"]
        r = run_release(kit, "--no-push", "--verify", 'test "$GOH_RELEASE_VERSION" = 9.9.9')
        assert r.returncode != 0
        assert "'verify'" in r.stdout + r.stderr
        assert not (proj / ".git" / "refs" / "tags" / TAG).exists()
        r = run_release(kit, "--no-push", "--verify", f'test "$GOH_RELEASE_VERSION" = {VERSION}')
        assert r.returncode == 0, r.stdout + r.stderr
        assert sh(proj, "cat-file", "-t", TAG).strip() == "tag"

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

    def test_push_pushes_branch_and_tag_to_origin(self, kit):
        r = run_release(kit)
        assert r.returncode == 0, r.stdout + r.stderr
        refs = sh(kit["origin"], "rev-parse", "--verify", f"refs/tags/{TAG}")
        assert refs.strip() == sh(kit["proj"], "rev-parse", TAG).strip()
        assert (
            sh(kit["origin"], "rev-parse", "--verify", "refs/heads/main").strip()
            == sh(kit["proj"], "rev-parse", "HEAD").strip()
        )

    def test_no_push_leaves_origin_untouched(self, kit):
        r = run_release(kit, "--no-push", "--skip-release")
        assert r.returncode == 0, r.stdout + r.stderr
        out = sh(kit["origin"], "tag", "-l")
        assert TAG not in out


# ── idempotency ──────────────────────────────────────────────────────────────


class TestIdempotency:
    def test_second_run_skips_existing_tag(self, kit):
        assert run_release(kit).returncode == 0
        first = sh(kit["proj"], "rev-parse", TAG).strip()

        kit["git_log"].write_text("")
        r = run_release(kit)
        assert r.returncode == 0, r.stdout + r.stderr

        calls = kit["git_log"].read_text()
        assert "git tag -a" not in calls, calls
        assert "already exists" in r.stdout
        assert sh(kit["proj"], "rev-parse", TAG).strip() == first

    def test_second_run_skips_existing_github_release(self, kit):
        assert run_release(kit).returncode == 0
        creates = kit["gh_log"].read_text().count(f"gh release create {TAG}")
        assert creates == 1

        r = run_release(kit)
        assert r.returncode == 0, r.stdout + r.stderr
        creates = kit["gh_log"].read_text().count(f"gh release create {TAG}")
        assert creates == 1, "second run re-created the GitHub release"


# ── dry run ──────────────────────────────────────────────────────────────────


class TestDryRun:
    def test_the_buffered_copy_runs_without_a_shell_error(self, kit):
        """The self-buffered copy runs from $TMPDIR, where `../../gates/_from_head.sh` is nothing:
        every release since 4cc8976 printed "No such file" and "command not found" first."""
        r = run_release(kit, "--dry-run")
        assert r.returncode == 0, r.stdout + r.stderr
        for sign in ("No such file", "command not found", "syntax error"):
            assert sign not in r.stderr, r.stderr

    def test_dry_run_has_zero_side_effects(self, kit):
        marker = kit["proj"] / "gate-marker"
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

        assert "[dry-run]" in r.stdout
        assert not marker.exists(), "dry-run executed the gate"
        out = sh(kit["proj"], "tag", "-l")
        assert TAG not in out, "dry-run created the tag"
        refs = sh(kit["origin"], "for-each-ref").strip()
        assert TAG not in refs, "dry-run pushed the tag"
        assert (
            sh(kit["origin"], "rev-parse", "--verify", "refs/heads/main").strip()
            == sh(kit["proj"], "rev-parse", "HEAD").strip()
        ), "dry-run pushed commits"
        assert not kit["gh_log"].exists() or "create" not in kit["gh_log"].read_text()


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
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True)
    return seed, bare


def bare_show(bare: Path, ref: str, rel: str) -> str:
    """Read a file straight out of the bare tap repo (authoritative, no clone)."""
    return subprocess.run(
        ["git", "-C", str(bare), "show", f"{ref}:{rel}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def bare_head(bare: Path) -> str:
    return sh(bare, "rev-parse", "--verify", "refs/heads/main").strip()


class TestTap:
    def test_bump_rewrites_url_and_sha_and_pushes(self, kit, tap):
        _, bare = tap
        artifact = kit["proj"] / "artifact.tar.gz"
        artifact.write_bytes(b"payload-bytes")

        r = run_release(kit, "--tap", str(bare), "--cask", "foo", "--artifact", str(artifact))
        assert r.returncode == 0, r.stdout + r.stderr

        import hashlib

        want_sha = hashlib.sha256(b"payload-bytes").hexdigest()
        content = bare_show(bare, "main", "Casks/foo.rb")
        assert f"/tags/{TAG}.tar.gz" in content
        assert f'sha256 "{want_sha}"' in content
        assert "v0.0.0" not in content

    def test_unchanged_formula_is_not_recommitted(self, kit, tap):
        _, bare = tap
        artifact = kit["proj"] / "artifact.tar.gz"
        artifact.write_bytes(b"payload-bytes")
        args = ("--tap", str(bare), "--cask", "foo", "--artifact", str(artifact))
        assert run_release(kit, *args).returncode == 0
        head_before = bare_head(bare)

        r = run_release(kit, *args)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "nothing to commit" in r.stdout
        assert bare_head(bare) == head_before
