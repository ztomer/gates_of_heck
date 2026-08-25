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


def run_release(kit: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(RELEASE), "--version", VERSION, "--gate", "true", *args],
        cwd=kit["proj"], capture_output=True, text=True, env=release_env(kit),
    )


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
