"""A Homebrew tap is bumped through its own gate (`crates/goh/src/tapwrite/`).

Until 2026-10-17 three producers wrote the tap around `tools/release-kit/tap_bump.sh`: antiknob and
monitor PUT the formula through the GitHub contents API, CadGoose's release workflow pushed from a
clone it made with a PAT. The tap's gate never judged those bumps. What a direct write is lives
beside the code (`tapwrite/tests.rs`, mutation-proven on both incident rows); this file is the
whole-repo behaviour: the incidents as written, index truth, an extensionless script, the reasoned
exemption, no path exemption, the dispatcher, and the structural step.

Every fixture that holds a tap name or the marker is assembled from pieces, so no line of THIS file
is itself a direct write or a stale marker under the gate it tests.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, hermetic_env, run_goh, write

LABEL = "no direct write to a Homebrew tap"
TAP = "ztomer/home" + "brew-tap"
MARKER = "tap-write" + "-ok:"
# antiknob's release before T2.1 (`4e6092b^:release.sh`), trimmed to the write.
API_WRITE = (
    f'TAP="{TAP}"\n'
    'CASK_PATH="Casks/antiknob.rb"\n'
    'gh api -X PUT "repos/${TAP}/contents/${CASK_PATH}" \\\n'
    '  -f message="antiknob ${VER}" >/dev/null\n'
)
# CadGoose's release workflow before T2.1 (`84a40b4^`), trimmed to the clone and the push.
CLONE_PUSH = (
    "jobs:\n  mac:\n    steps:\n      - name: Update Homebrew Cask in Tap\n        run: |\n"
    f'          git -c "$TAP_HEADER" clone https://github.com/{TAP}.git /tmp/tap\n'
    "          cd /tmp/tap\n"
    '          git -c "$TAP_HEADER" push origin main\n'
)
# The same release after it.
GATED = f'"$GOH_DIR/tools/release-kit/tap_bump.sh" --tap {TAP} --cask antiknob --tag "$TAG" --artifact "$DMG"\n'


def _structural(goh: Path, repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(goh), "structural", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_the_api_write_is_named_at_its_line_with_the_fix(repo: Path) -> None:
    write(repo, "release.sh", API_WRITE)
    commit_all(repo)
    r = run_goh(repo, "tap-writes")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "release.sh:3: writes a Homebrew tap's contents through the GitHub API" in r.stderr, (
        r.stderr
    )
    assert "tools/release-kit/tap_bump.sh" in r.stderr, r.stderr  # the fix is named
    assert f"# {MARKER} <reason>" in r.stderr, r.stderr  # and the exemption


def test_the_push_from_a_clone_is_named_at_the_push(repo: Path) -> None:
    write(repo, ".github/workflows/release.yml", CLONE_PUSH)
    commit_all(repo)
    r = run_goh(repo, "tap-writes")
    assert r.returncode == 1, r.stdout + r.stderr
    assert (
        ".github/workflows/release.yml:8: pushes from the Homebrew tap cloned on line 6" in r.stderr
    ), r.stderr


def test_the_gated_release_passes(repo: Path) -> None:
    write(repo, "release.sh", GATED)
    commit_all(repo)
    r = run_goh(repo, "tap-writes")
    assert r.returncode == 0 and "1 tracked script/workflow file" in r.stdout, r.stdout + r.stderr


def test_staged_judges_the_index_not_the_worktree(repo: Path) -> None:
    write(repo, "release.sh", API_WRITE)
    git(repo, "add", "release.sh")
    write(repo, "release.sh", GATED)  # the worktree is clean; the commit is not
    r = run_goh(repo, "tap-writes", "--staged")
    assert r.returncode == 1 and "release.sh:3:" in r.stderr, r.stdout + r.stderr
    assert run_goh(repo, "tap-writes").returncode == 0  # the worktree's own verdict


def test_an_extensionless_script_is_read_by_its_shebang(repo: Path) -> None:
    write(repo, "bin/release", "#!/usr/bin/env bash\n" + API_WRITE)
    write(repo, "notes/release", API_WRITE)  # no shebang: not a script anything runs
    commit_all(repo)
    r = run_goh(repo, "tap-writes")
    assert r.returncode == 1 and "bin/release:4:" in r.stderr, r.stdout + r.stderr
    assert "notes/release" not in r.stderr, r.stderr


def test_a_reasoned_exemption_passes_and_an_empty_one_does_not(repo: Path) -> None:
    write(repo, "release.sh", f"# {MARKER} the tap's own repair script, run by hand\n" + API_WRITE)
    commit_all(repo)
    assert (
        run_goh(repo, "tap-writes").returncode == 1
    )  # the marker sits above line 1, not the write
    lines = API_WRITE.splitlines(keepends=True)
    write(
        repo,
        "release.sh",
        "".join(lines[:2]) + f"# {MARKER} the tap's own repair script\n" + "".join(lines[2:]),
    )
    commit_all(repo)
    assert run_goh(repo, "tap-writes").returncode == 0
    write(repo, "release.sh", "".join(lines[:2]) + f"# {MARKER}\n" + "".join(lines[2:]))
    commit_all(repo)
    r = run_goh(repo, "tap-writes")
    assert r.returncode == 1 and "with no reason" in r.stderr, r.stdout + r.stderr


def test_no_path_exemption_reaches_it(repo: Path) -> None:
    """A gate bypass takes no path exemption: `GOH_EXCLUDE` is about style."""
    write(repo, "release.sh", API_WRITE)
    write(repo, ".gatesrc", "GOH_EXCLUDE='^release\\.sh$'\n")
    commit_all(repo)
    assert run_goh(repo, "tap-writes").returncode == 1
    r = run_goh(repo, "tap-writes", "--exclude", "x")
    assert r.returncode == 2, r.stdout + r.stderr  # no such flag


def test_the_dispatcher_knows_it(repo: Path) -> None:
    write(repo, "release.sh", API_WRITE)
    commit_all(repo)
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "goh.sh"), "tap-writes"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 1 and "unknown check" not in r.stderr, r.stdout + r.stderr


def test_the_structural_step_fails_at_both_scopes(goh: Path, repo: Path) -> None:
    # The workflow shape: a shell fixture would stop at `shell lint` first, which runs earlier.
    write(repo, ".github/workflows/release.yml", CLONE_PUSH)
    git(repo, "add", ".github/workflows/release.yml")
    staged = _structural(goh, repo, "--staged")
    assert staged.returncode == 1 and f"structural: {LABEL}" in staged.stderr, staged.stderr
    commit_all(repo)
    full = _structural(goh, repo, "--full")
    assert full.returncode == 1 and f"structural: {LABEL}" in full.stderr, full.stderr
