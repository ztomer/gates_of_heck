"""changelog-tags refuses to judge a clone whose tags it cannot trust (GOH.TAGLESSCI, 2026-11-11).

On divoom-control 331be031 the local run found 16 `## vX names no tag` findings, inside its seed of
18; the hosted runner (goh v0.96.0, run 43136219614) found NONE and failed the seed as stale. The
runner's `actions/checkout` is shallow (fetch-depth 1): a branch push fetches no tag, a tag push
fetches the ONE it pushed, and either way the check compared the changelog with a tag set that is
not the repository's and read the result as clean.

Now a shallow clone, or a clone with no `v*` tag whose changelog has version headings, is NOT
JUDGED: the run says why and how to fetch the tags, exits 0 and calls no seed entry stale -- it
never prints the pass line that a judged, clean changelog prints.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import commit_all, git, hermetic_env, run_goh, write

CHANGELOG = "# Changelog\n\n## v0.3.0\n\n* c\n\n## v0.2.0\n\n* b\n\n## v0.1.0\n\n* a\n"


def _origin(repo: Path) -> str:
    """A repository whose changelog has one heading no tag names (v0.2.0), seeded as one finding."""
    write(repo, "CHANGELOG.md", CHANGELOG)
    commit_all(repo)
    git(repo, "tag", "v0.1.0")
    root = git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    entry = {"root": root, "repo": "fixture", "path": "CHANGELOG.md", "count": 1, "reason": "test"}
    write(repo, "changelog_tags_seed.json", json.dumps({"why": "t", "entries": [entry]}) + "\n")
    commit_all(repo, "seed")
    git(repo, "tag", "v0.3.0")
    return root


def _clone(src: Path, dst: Path, *args: str) -> Path:
    subprocess.run(
        ["git", "clone", "-q", *args, f"file://{src}", str(dst)],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    return dst


def _run(repo: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    store = tmp_path / "no-store"
    return run_goh(repo, "changelog-tags", env=hermetic_env(GOH_SEED_DIR=str(store)))


def test_the_full_clone_is_judged_and_its_finding_is_the_seeds(repo: Path, tmp_path: Path) -> None:
    _origin(repo)
    full = _clone(repo, tmp_path / "full")
    r = _run(full, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "`## v0.2.0` names no tag" in r.stderr, r.stderr
    assert "NOT JUDGED" not in r.stdout + r.stderr


def test_a_shallow_branch_clone_with_no_tag_is_not_judged(repo: Path, tmp_path: Path) -> None:
    _origin(repo)
    shallow = _clone(repo, tmp_path / "shallow", "--depth", "1", "--no-tags")
    r = _run(shallow, tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "NOT JUDGED" in out and "shallow" in out, out
    assert "fetch-depth: 0" in out, "the reason names the fix: " + out
    assert " OK " not in out and "stale" not in out, out


def test_a_shallow_tag_push_clone_is_not_judged_rather_than_stale(
    repo: Path, tmp_path: Path
) -> None:
    """A tag push's checkout holds only the tag pushed: v0.3.0 alone hides v0.2.0's finding."""
    _origin(repo)
    shallow = _clone(repo, tmp_path / "tagpush", "--depth", "1", "--branch", "v0.3.0")
    git(shallow, "fetch", "-q", "--depth", "1", "origin", "tag", "v0.3.0")
    r = _run(shallow, tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 0, "the seed is not stale for a clone that could not judge: " + out
    assert "NOT JUDGED" in out and "shallow" in out, out


def test_a_full_clone_with_no_tags_is_not_judged(repo: Path, tmp_path: Path) -> None:
    _origin(repo)
    tagless = _clone(repo, tmp_path / "tagless", "--no-tags")
    r = _run(tagless, tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "NOT JUDGED" in out and "no v* tag" in out, out
    assert " OK " not in out, out


def test_a_changelog_with_no_version_heading_and_no_tag_is_judged(
    repo: Path, tmp_path: Path
) -> None:
    """A repo before its first release has nothing to compare: a clean pass, not a refusal."""
    write(repo, "CHANGELOG.md", "# Changelog\n\n## Unreleased\n\n* a\n")
    commit_all(repo)
    r = _run(repo, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "NOT JUDGED" not in r.stdout + r.stderr, r.stdout + r.stderr
