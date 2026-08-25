"""tools/release-kit/release.sh — hardening behaviors reported from the field.

Contract pinned here (beyond test_release_kit.py):
  --no-push skips BOTH the push step and the GitHub-release step: gh release
  create against an unpushed tag cannot succeed. Every skipped step names
  itself and its reason, and the run summary repeats them.

git is REAL throughout (local bare remote); gh is the stateful stub shared
with test_release_kit.py.

Red proof: these tests were written FIRST and failed against pre-fix
release.sh (gh release create ran against an unpushed tag; no skip reasons
printed). They went green only after the fix landed in the same commit.
"""

import os
import subprocess
import time
from pathlib import Path

import pytest

from conftest import REPO_ROOT

RELEASE = REPO_ROOT / "tools" / "release-kit" / "release.sh"

VERSION = "1.2.3"
TAG = f"v{VERSION}"
STANZA_BODY = "- Added the release kit\n- Fixed seven drifting releasers"

FAKE_GH = """\
#!/usr/bin/env bash
STATE="${GH_STATE:?}"; LOG="${GH_LOG:?}"
printf 'gh %s\\n' "$*" >> "$LOG"
cmd="$1"; shift
case "$cmd" in
  auth) exit 0 ;;
  api) exit 0 ;;
  release)
    sub="$1"; shift
    case "$sub" in
      view)
        tag=""
        while [ $# -gt 0 ]; do
          case "$1" in
            --repo) shift 2 ;;
            --*) shift ;;
            *) [ -z "$tag" ] && tag="$1"; shift ;;
          esac
        done
        [ -n "$tag" ] && [ -f "$STATE/rel-$tag" ] ;;
      create)
        tag=""; notes=""
        while [ $# -gt 0 ]; do
          case "$1" in
            --notes-file) notes="$2"; shift 2 ;;
            --title|--repo) shift 2 ;;
            *) [ -z "$tag" ] && tag="$1"; shift ;;
          esac
        done
        : > "$STATE/rel-$tag"
        [ -n "$notes" ] && cp "$notes" "$STATE/notes-$tag"
        exit 0 ;;
      *) exit 0 ;;
    esac ;;
  *) exit 0 ;;
esac
"""


def sh(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return r.stdout


def commit_all(repo: Path, msg: str = "fixture") -> None:
    sh(repo, "add", "-A")
    sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", msg)


def gh_calls(kit: dict) -> str:
    log = kit["gh_log"]
    return log.read_text() if log.exists() else ""


def release_env(kit: dict) -> dict:
    env = dict(os.environ)
    env["GOH_DIR"] = str(REPO_ROOT)
    env["PATH"] = f"{kit['bin']}:{env['PATH']}"
    env["GH_STATE"] = str(kit["gh_state"])
    env["GH_LOG"] = str(kit["gh_log"])
    env.pop("GOH_RELEASE_GATE", None)
    return env


def run_release(kit: dict, *args: str, version: str = VERSION) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(RELEASE), "--version", version, "--gate", "true", *args],
        cwd=kit["proj"], capture_output=True, text=True, env=release_env(kit),
    )


def tag_message(repo: Path, tag: str) -> str:
    """The annotated tag's message (%(contents)), stripped."""
    return sh(repo, "tag", "-l", tag, "--format=%(contents)").strip()


@pytest.fixture
def kit(tmp_path: Path) -> dict:
    """A real project repo with a bare origin, stanza'd CHANGELOG, fake gh."""
    proj = tmp_path / "proj"
    proj.mkdir()
    sh(proj, "init", "-q", "-b", "main")
    (proj / "CHANGELOG.md").write_text(
        "# CHANGELOG\n\n"
        f"## {TAG}\n\n{STANZA_BODY}\n\n"
        "## v1.1.0\n\n- older\n",
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
    return {
        "proj": proj,
        "origin": origin,
        "bin": bin_dir,
        "gh_state": gh_state,
        "gh_log": tmp_path / "gh.log",
    }


class TestNoPushImpliesNoRelease:
    def test_no_push_skips_push_and_release_and_says_why(self, kit):
        r = run_release(kit, "--no-push")
        assert r.returncode == 0, r.stdout + r.stderr

        out = r.stdout + r.stderr
        # Both skips announced, each with its reason.
        assert "push" in out and "skipped" in out
        assert "--no-push" in out
        assert "implied by --no-push" in out, out
        assert TAG in out.split("implied by --no-push")[1], (
            "release-skip reason must name the unpushed tag"
        )

        # The tag exists LOCALLY but never reached origin...
        assert sh(kit["proj"], "rev-parse", "--verify", f"refs/tags/{TAG}")
        refs = sh(kit["origin"], "for-each-ref")
        assert TAG not in refs

        # ...and gh was never invoked at all.
        assert "release" not in gh_calls(kit), gh_calls(kit)

    def test_no_push_plus_skip_release_is_also_fine(self, kit):
        r = run_release(kit, "--no-push", "--skip-release")
        assert r.returncode == 0, r.stdout + r.stderr
        out = r.stdout + r.stderr
        assert "--skip-release" in out
        assert "release" not in gh_calls(kit), gh_calls(kit)

    def test_default_still_creates_the_release(self, kit):
        """Control: the implied skip must not leak into default runs."""
        r = run_release(kit)
        assert r.returncode == 0, r.stdout + r.stderr
        assert f"gh release create {TAG}" in gh_calls(kit)


class TestSelfBuffering:
    def test_mid_run_edit_completes_with_original_semantics(self, kit, tmp_path):
        """Editing release.sh while it runs must not corrupt a running release.

        bash parses scripts lazily by byte offset; before the self-buffering
        exec, rewriting the file mid-run (the field incident) shifted every
        unread offset and crashed or garbled the rest of the run. The gate is
        the slow point: it touches a marker (proving release.sh is mid-run),
        then sleeps while the test swaps the script for different-length
        content. The invocation must still complete with the ORIGINAL
        content's semantics: full push + GitHub release of the stanza body.
        """
        marker = tmp_path / "gate-started"
        proc = subprocess.Popen(
            ["/bin/bash", str(RELEASE), "--version", VERSION,
             "--gate", f"touch {marker} && sleep 5"],
            cwd=kit["proj"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=release_env(kit),
        )
        original = RELEASE.read_bytes()
        try:
            deadline = time.monotonic() + 30
            while not marker.exists():
                assert time.monotonic() < deadline, "gate never started"
                assert proc.poll() is None, "release.sh exited before the edit"
                time.sleep(0.05)

            RELEASE.write_bytes(
                b"#!/usr/bin/env bash\necho corrupted mid-run ((\n")
            out, err = proc.communicate(timeout=120)
        finally:
            RELEASE.write_bytes(original)

        assert proc.returncode == 0, out + err
        assert "corrupted" not in out + err
        # Original semantics completed end to end:
        refs = sh(kit["origin"], "for-each-ref")
        assert TAG in refs, "tag was never pushed"
        calls = gh_calls(kit)
        assert f"gh release create {TAG}" in calls, calls
        notes = (kit["gh_state"] / f"notes-{TAG}").read_text()
        assert STANZA_BODY.splitlines()[0] in notes


class TestRegressionPins:
    """Pins for bugs already fixed on main that had no dedicated test.

    Red proofs: the X.Y pin went red with the validator reverted to
    three-components-only; the missing-CHANGELOG pin went red with the
    `[ -f CHANGELOG.md ] || return 0` guard dropped (awk under set -e
    aborted the tag step). The subheading pin was red against the shipped
    awk itself: `/^##+ /` treats `###` subheadings as stanza ends, so a
    Keep-a-changelog body collapsed to the tag-name fallback.
    """

    def test_two_component_version_end_to_end(self, kit):
        """X.Y versions are valid — CadGoose's tag scheme is v1.71..v1.79."""
        (kit["proj"] / "CHANGELOG.md").write_text(
            "# CHANGELOG\n\n## v1.79\n\n- cadgoose-style two-component cut\n",
            encoding="utf-8",
        )
        commit_all(kit["proj"])
        r = run_release(kit, "--no-push", version="1.79")
        assert r.returncode == 0, r.stdout + r.stderr
        assert sh(kit["proj"], "cat-file", "-t", "v1.79").strip() == "tag"
        assert "cadgoose-style two-component cut" in tag_message(kit["proj"], "v1.79")

    def test_missing_changelog_file_still_tags_with_name_fallback(self, kit):
        """--no-changelog-check + no CHANGELOG.md: tag step must survive.

        The stanza body is empty, so the message falls back to the tag name.
        """
        (kit["proj"] / "CHANGELOG.md").unlink()
        commit_all(kit["proj"])
        r = run_release(kit, "--no-changelog-check", "--no-push")
        assert r.returncode == 0, r.stdout + r.stderr
        assert sh(kit["proj"], "cat-file", "-t", TAG).strip() == "tag"
        assert tag_message(kit["proj"], TAG) == TAG

    def test_stanza_body_keeps_subheadings_and_stops_at_next_stanza(self, kit):
        """FULL Keep-a-changelog body lands in the tag message; no bleed."""
        (kit["proj"] / "CHANGELOG.md").write_text(
            "# CHANGELOG\n"
            "\n"
            f"## {TAG}\n"
            "\n"
            "### Added\n"
            "\n"
            "- the release kit\n"
            "\n"
            "### Fixed\n"
            "\n"
            "- seven drifting releasers\n"
            "\n"
            "## v1.1.0\n"
            "\n"
            "- older\n",
            encoding="utf-8",
        )
        commit_all(kit["proj"])
        r = run_release(kit)
        assert r.returncode == 0, r.stdout + r.stderr

        msg = tag_message(kit["proj"], TAG)
        for want in ("### Added", "- the release kit", "### Fixed",
                     "- seven drifting releasers"):
            assert want in msg, f"missing {want!r} in tag message:\n{msg}"
        assert "older" not in msg, "body bled into the next (older) stanza"

        # The gh release notes carry the same full body.
        assert f"gh release create {TAG}" in gh_calls(kit)
        notes = (kit["gh_state"] / f"notes-{TAG}").read_text()
        assert "### Added" in notes and "older" not in notes


# ── stanza matching: the version regex must reach awk VERBATIM ───────────────


def _bare_tap(tmp_path: Path) -> Path:
    """A bare tap repo seeded with a cask pinned at v0.0.0."""
    seed = tmp_path / "tap-seed"
    seed.mkdir()
    sh(seed, "init", "-q", "-b", "main")
    casks = seed / "Casks"
    casks.mkdir()
    (casks / "foo.rb").write_text(
        'cask "foo" do\n'
        '  url "https://example.com/foo/archive/refs/tags/v0.0.0.tar.gz"\n'
        '  sha256 "0000000000000000000000000000000000000000000000000000000000000000"\n'
        "end\n",
        encoding="utf-8",
    )
    commit_all(seed)
    bare = tmp_path / "tap.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True)
    return bare


class TestStanzaMatchIsVerbatim:
    """awk -v processed backslash escapes, so v1\\.2\\.3 arrived as bare
    wildcards and bogus headings matched the stanza (## v1x2y3, ## v19283).
    Red proof: both decoys below matched pre-fix, leaking their bodies into
    the tag message."""

    DECOYS = (
        "# CHANGELOG\n\n"
        "## v1x2y3\n\n- wildcard decoy body\n\n"
        "## v19283\n\n- numeric decoy body\n\n"
        f"## {TAG}\n\n{STANZA_BODY}\n\n"
        "## v1.1.0\n\n- older\n"
    )

    def test_bogus_headings_do_not_match_the_stanza(self, kit):
        (kit["proj"] / "CHANGELOG.md").write_text(self.DECOYS, encoding="utf-8")
        commit_all(kit["proj"])
        r = run_release(kit, "--no-push")
        assert r.returncode == 0, r.stdout + r.stderr
        msg = tag_message(kit["proj"], TAG)
        assert STANZA_BODY in msg, msg
        assert "decoy" not in msg, "a bogus heading's body leaked into the tag"

    def test_real_stanza_still_matches_with_bracketed_form(self, kit):
        (kit["proj"] / "CHANGELOG.md").write_text(
            "# CHANGELOG\n\n" f"## [{VERSION}]\n\n{STANZA_BODY}\n",
            encoding="utf-8",
        )
        commit_all(kit["proj"])
        r = run_release(kit, "--no-push")
        assert r.returncode == 0, r.stdout + r.stderr
        assert tag_message(kit["proj"], TAG) == STANZA_BODY


# ── stale tag guard ──────────────────────────────────────────────────────────


class TestStaleTagGuard:
    def test_tag_at_older_commit_hard_fails_naming_both_shas(self, kit):
        assert run_release(kit, "--no-push").returncode == 0
        # Annotated tag: dereference to the commit it pins (what the guard
        # compares).
        tagged = sh(kit["proj"], "rev-parse", f"{TAG}^{{commit}}").strip()

        # Work moved on AFTER the tag was cut.
        (kit["proj"] / "later.txt").write_text("later work\n", encoding="utf-8")
        commit_all(kit["proj"])
        head = sh(kit["proj"], "rev-parse", "HEAD").strip()
        assert head != tagged

        r = run_release(kit, "--no-push")
        assert r.returncode != 0, "stale tag released the WRONG tree silently"
        combined = r.stdout + r.stderr
        assert tagged in combined, "the tag's pinned SHA must be named"
        assert head in combined, "HEAD's SHA must be named"
        assert "git tag -f" in combined, "remediation must be stated"

    def test_same_tree_rerun_is_still_idempotent(self, kit):
        assert run_release(kit, "--no-push").returncode == 0
        first = sh(kit["proj"], "rev-parse", f"{TAG}^{{commit}}").strip()
        r = run_release(kit, "--no-push")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "already exists" in r.stdout
        assert sh(kit["proj"], "rev-parse", f"{TAG}^{{commit}}").strip() == first


# ── artifact download failures are named, not laundered ──────────────────────


class TestArtifactDownloadFailure:
    def test_unreachable_url_is_a_named_failure_not_an_empty_hash(self, kit, tmp_path):
        """Red proof: `curl | shasum` under set -e let shasum hash EMPTY input
        and the tap got the digest of nothing, reported as success."""
        bare = _bare_tap(tmp_path)
        bad_url = "http://127.0.0.1:9/nope.tar.gz"  # nothing listens; instant refuse
        r = run_release(kit, "--no-push", "--tap", str(bare), "--cask", "foo",
                        "--artifact", bad_url)
        assert r.returncode == 1, r.stdout + r.stderr
        combined = r.stdout + r.stderr
        assert "could not download artifact" in combined
        assert bad_url in combined
        # The tap must be untouched — no empty-input digest committed.
        import hashlib
        content = subprocess.run(
            ["git", "-C", str(bare), "show", "main:Casks/foo.rb"],
            capture_output=True, text=True, check=True,
        ).stdout
        assert hashlib.sha256(b"").hexdigest() not in content
        assert 'sha256 "0000' in content
