"""A container image is pinned to one build, never to a moving tag (`crates/goh/src/movingtag/`).

Adv360-Pro-ZMK's Dockerfile and both CI jobs read `zmkfirmware/zmk-build-arm:stable` until its
P0.2 (2026-10-17), so a CI run and a local build could use different toolchains with nothing in
any diff saying so. What moves lives beside the code (`movingtag/tests.rs`, mutation-proven); this
file is the whole-repo behaviour: the incident as written and as fixed, index truth, a compose
file no filename rule would know, the reasoned exemption, no path exemption, the dispatcher, the
structural step at both scopes, and the subject floor.

Every fixture that holds the marker is assembled from pieces, so no line of THIS file reads as a
stale marker to a scanner that one day reads Python.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, hermetic_env, run_goh, write

LABEL = "no container image on a moving tag"
MARKER = "moving-tag" + "-ok:"
DIGEST = "sha256:840526f03c19c286b614d5cb97b11242ae19e468fc2ef2f095f6cf0465fa1c5b"
# Adv360-Pro-ZMK before P0.2, trimmed: the Dockerfile and one CI job.
DOCKERFILE_MOVING = "FROM zmkfirmware/zmk-build-arm:stable\nWORKDIR /app\n"
WORKFLOW_MOVING = (
    "jobs:\n  build:\n    runs-on: ubuntu-latest\n    container:\n"
    "      image: zmkfirmware/zmk-build-arm:stable\n"
)
# ...and after: the dated release tag plus its digest.
PINNED = f"zmkfirmware/zmk-build-arm:3.5-branch-20250930025731-3.5.0-0.16.3-f93290b3ab1a-18117245422@{DIGEST}"
DOCKERFILE_PINNED = f"FROM {PINNED}\nWORKDIR /app\n"


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


def test_the_incident_is_named_at_each_line_with_the_fix(repo: Path) -> None:
    write(repo, "Dockerfile", DOCKERFILE_MOVING)
    write(repo, ".github/workflows/build.yml", WORKFLOW_MOVING)
    commit_all(repo)
    r = run_goh(repo, "moving-tags")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Dockerfile:1: `zmkfirmware/zmk-build-arm:stable` is on the moving tag `stable`" in (
        r.stderr
    ), r.stderr
    assert ".github/workflows/build.yml:5: " in r.stderr, r.stderr
    assert "@sha256:" in r.stderr, r.stderr  # the fix is named
    assert f"# {MARKER} <reason>" in r.stderr, r.stderr  # and the exemption


def test_the_pinned_tree_passes_and_says_what_it_read(repo: Path) -> None:
    write(repo, "Dockerfile", DOCKERFILE_PINNED)
    write(
        repo,
        ".github/workflows/build.yml",
        WORKFLOW_MOVING.replace("zmkfirmware/zmk-build-arm:stable", PINNED),
    )
    commit_all(repo)
    r = run_goh(repo, "moving-tags")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "2 image reference(s): 2 pinned by digest" in r.stdout, r.stdout


def test_a_commit_pin_is_green_and_the_run_says_which_pin_it_is(repo: Path) -> None:
    """THE COMMIT PIN (2026-10-21). A tag that IS a commit id names one build, and nobody can
    re-point it: the other pin nothing can move, beside the digest.

    The house names the two apart, because a gate that cannot EXPRESS a pin cannot accept one --
    Adv360-Pro-ZMK's base-pin row (P1.3) is blocked on its own gate being able to say "this is
    pinned to a commit", and its branch that said it was unreachable: the check went red before it
    got there. So the run says which pin each reference carries.
    """
    write(repo, "Dockerfile", "FROM zmkfirmware/zmk-build-arm:f93290b3ab1a\n")
    commit_all(repo)
    r = run_goh(repo, "moving-tags")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 pinned by commit" in r.stdout, r.stdout


def test_a_tag_that_carries_a_commit_id_is_a_release_not_a_commit_pin(repo: Path) -> None:
    """A tag that names MORE than the commit (a version, a date, a build) can be re-pointed: it
    is a release, and the house says so."""
    write(repo, "Dockerfile", "FROM zmkfirmware/zmk-build-arm:3.5.0-f93290b3ab1a\n")
    commit_all(repo)
    r = run_goh(repo, "moving-tags")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 pinned by commit" not in r.stdout, r.stdout
    assert "1 image reference(s)" in r.stdout, r.stdout


def test_no_tag_is_latest(repo: Path) -> None:
    write(repo, "compose/media.yml", "services:\n  cal:\n    image: ghcr.io/linuxserver/calibre\n")
    commit_all(repo)
    r = run_goh(repo, "moving-tags")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "compose/media.yml:3: `ghcr.io/linuxserver/calibre` has no tag" in r.stderr, r.stderr


def test_staged_judges_the_index_not_the_worktree(repo: Path) -> None:
    write(repo, "Dockerfile", DOCKERFILE_MOVING)
    git(repo, "add", "Dockerfile")
    write(repo, "Dockerfile", DOCKERFILE_PINNED)  # the worktree is clean; the commit is not
    r = run_goh(repo, "moving-tags", "--staged")
    assert r.returncode == 1 and "Dockerfile:1:" in r.stderr, r.stdout + r.stderr
    commit_all(repo)
    assert run_goh(repo, "moving-tags").returncode == 0  # the committed worktree's own verdict


def test_a_reasoned_exemption_passes_an_empty_or_stale_one_does_not(repo: Path) -> None:
    body = "services:\n  web:\n    {m}\n    image: nginx:latest\n"
    write(repo, "compose.yml", body.format(m=f"# {MARKER} floats by owner ruling (DECISIONS #9)"))
    commit_all(repo)
    r = run_goh(repo, "moving-tags")
    assert r.returncode == 0 and "1 exempted with a reason" in r.stdout, r.stdout + r.stderr
    write(repo, "compose.yml", body.format(m=f"# {MARKER}"))
    commit_all(repo)
    r = run_goh(repo, "moving-tags")
    assert r.returncode == 1 and "compose.yml:4: " in r.stderr and "with no reason" in r.stderr, (
        r.stdout + r.stderr
    )
    write(
        repo, "compose.yml", body.format(m=f"# {MARKER} r").replace("nginx:latest", "nginx:1.27.2")
    )
    commit_all(repo)
    r = run_goh(repo, "moving-tags")
    assert r.returncode == 1 and "compose.yml:3: " in r.stderr and "exempts nothing" in r.stderr, (
        r.stdout + r.stderr
    )


def test_no_path_exemption_reaches_it(repo: Path) -> None:
    """What a build pulls is not a style: `GOH_EXCLUDE` does not reach it."""
    write(repo, "vendor/Dockerfile", DOCKERFILE_MOVING)
    write(repo, ".gatesrc", "GOH_EXCLUDE='^vendor/'\n")
    commit_all(repo)
    assert run_goh(repo, "moving-tags").returncode == 1
    r = run_goh(repo, "moving-tags", "--exclude", "x")
    assert r.returncode == 2, r.stdout + r.stderr  # no such flag


def test_the_dispatcher_knows_it(repo: Path) -> None:
    write(repo, "Dockerfile", DOCKERFILE_MOVING)
    commit_all(repo)
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "goh.sh"), "moving-tags"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 1 and "unknown check" not in r.stderr, r.stdout + r.stderr


def test_the_structural_step_fails_at_both_scopes(goh: Path, repo: Path) -> None:
    write(repo, "Dockerfile", DOCKERFILE_MOVING)
    git(repo, "add", "Dockerfile")
    staged = _structural(goh, repo, "--staged")
    assert staged.returncode == 1 and f"structural: {LABEL}" in staged.stderr, staged.stderr
    commit_all(repo)
    full = _structural(goh, repo, "--full")
    assert full.returncode == 1 and f"structural: {LABEL}" in full.stderr, full.stderr


def test_the_floor_reds_a_scan_that_lost_its_subject(goh: Path, repo: Path) -> None:
    """`GOH_MOVING_TAGS_MIN_REFS`: one reference read, a floor of two is red, one is green."""
    write(repo, "Dockerfile", DOCKERFILE_PINNED)
    write(repo, ".gatesrc", "GOH_MOVING_TAGS_MIN_REFS=2\n")
    commit_all(repo)
    below = "`no container image on a moving tag` read 1 image reference(s) and .gatesrc declares a floor of 2"
    red = _structural(goh, repo, "--full", "--keep-going")
    assert red.returncode == 1 and below in red.stdout + red.stderr, red.stdout + red.stderr
    write(repo, ".gatesrc", "GOH_MOVING_TAGS_MIN_REFS=1\n")
    commit_all(repo)
    green = _structural(goh, repo, "--full", "--keep-going")
    assert "floor of 1 (GOH_MOVING_TAGS_MIN_REFS)" not in green.stdout + green.stderr, (
        green.stdout + green.stderr
    )
