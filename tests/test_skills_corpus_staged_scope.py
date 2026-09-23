"""The skills-corpus step at --staged polices the INDEX, not the working tree.

Observed 2026-09-23 in ~/.claude/skills: a commit staging three clean skills
was refused because OTHER skills, edited in the working tree by concurrent
sessions and never staged, were over the word ceiling or carried a dangling
[[link]]. The corpus step handed the checker `--root <worktree>` at both
scopes, so the pre-commit gate judged files the commit did not contain.

The class is "a pre-commit gate reads the working tree", and it fails in
BOTH directions, so both are pinned:

  - a clean staged change is blocked by an unstaged violation (false red);
  - a staged violation passes because its fix is merely unstaged (false
    green) -- the commit records the violation, the gate never saw it.

Both the Python pipeline (GOH_NO_NATIVE=1) and the native binary are driven,
and each case also runs --full to prove the violation is real to the checker:
--full must keep reading the tree, since "is my tree green" is its question.
"""
import os
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT, commit_all, git, stage, write

GOOD = "---\nname: {name}\ndescription: does a thing worth triggering on.\n---\n\n# {name}\n\nbody\n"
DANGLING = GOOD + "\nSee [[no-such-skill]].\n"


def _corpus_repo(repo: Path, sub: str) -> Path:
    """Six valid, committed skills under `repo/sub`, with the gate opted in."""
    root = repo / sub if sub else repo
    for i in range(6):
        write(root, f"skill-{i}/SKILL.md", GOOD.format(name=f"skill-{i}"))
    gatesrc = "GOH_MAX_LINES=500\nGOH_SKILLS_CORPUS=1\n"
    if sub:
        gatesrc += f"GOH_SKILLS_ROOT={root}\n"
    write(repo, ".gatesrc", gatesrc)
    commit_all(repo)
    return root


def _run(runner: str, goh: Path, repo: Path, scope: str, **extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, GOH_DIR=str(REPO_ROOT), **extra)
    env.pop("GOH_BIN", None)
    if runner == "python":
        env["GOH_NO_NATIVE"] = "1"
        cmd = ["bash", str(REPO_ROOT / "gates" / "structural.sh"), scope]
    else:
        env.pop("GOH_NO_NATIVE", None)
        cmd = [str(goh), "structural", scope]
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, env=env)


RUNNERS = pytest.mark.parametrize("runner", ["python", "native"])
# "" is the ~/.claude/skills shape (the corpus IS the repo); "skills" is a
# corpus in a subdirectory, named by an absolute GOH_SKILLS_ROOT.
LAYOUTS = pytest.mark.parametrize("sub", ["", "skills"])


@RUNNERS
@LAYOUTS
def test_a_clean_staged_change_passes_over_an_unstaged_violation(runner, sub, repo, goh):
    root = _corpus_repo(repo, sub)
    write(root, "skill-0/SKILL.md", GOOD.format(name="skill-0") + "\nmore\n")
    stage(repo, str((root / "skill-0/SKILL.md").relative_to(repo)))
    # Unstaged: a tracked skill gains a dangling link, and an untracked skill
    # with no frontmatter appears. Neither is in the commit.
    write(root, "skill-1/SKILL.md", DANGLING.format(name="skill-1"))
    write(root, "skill-new/SKILL.md", "# no frontmatter\n")

    staged = _run(runner, goh, repo, "--staged")
    assert staged.returncode == 0, staged.stdout + staged.stderr
    assert "skills corpus" in staged.stdout

    full = _run(runner, goh, repo, "--full")
    assert full.returncode == 1, full.stdout + full.stderr
    assert "no-such-skill" in full.stdout + full.stderr


@RUNNERS
@LAYOUTS
def test_a_staged_violation_fails_though_its_fix_is_unstaged(runner, sub, repo, goh):
    root = _corpus_repo(repo, sub)
    rel = str((root / "skill-1/SKILL.md").relative_to(repo))
    write(root, "skill-1/SKILL.md", DANGLING.format(name="skill-1"))
    stage(repo, rel)
    write(root, "skill-1/SKILL.md", GOOD.format(name="skill-1"))  # fixed, NOT staged

    staged = _run(runner, goh, repo, "--staged")
    assert staged.returncode == 1, staged.stdout + staged.stderr
    assert "no-such-skill" in staged.stdout + staged.stderr

    full = _run(runner, goh, repo, "--full")
    assert full.returncode == 0, full.stdout + full.stderr


@RUNNERS
def test_a_staged_deletion_is_absent_from_the_staged_corpus(runner, repo, goh):
    """`git rm --cached` leaves the file on disk; the commit drops it. The floor
    (5 skills) then bites at --staged and not at --full -- the index is what
    the checker counted."""
    _corpus_repo(repo, "")
    git(repo, "rm", "-q", "--cached", "skill-0/SKILL.md", "skill-1/SKILL.md")

    staged = _run(runner, goh, repo, "--staged")
    assert staged.returncode != 0, staged.stdout + staged.stderr
    assert "only 4 skill(s)" in staged.stdout + staged.stderr

    full = _run(runner, goh, repo, "--full")
    assert full.returncode == 0, full.stdout + full.stderr


@RUNNERS
def test_a_corpus_outside_the_repo_is_skipped_by_name_at_staged(runner, repo, goh, tmp_path):
    """GOH_SKILLS_ROOT outside the committing repo (gates_of_heck's own wiring of
    ~/.claude/skills) is no part of the commit, so a violation there can only
    ever be a false red at pre-commit scope. --staged names the skip; --full
    still reads it where it lies."""
    outside = tmp_path / "elsewhere"
    for i in range(6):
        write(outside, f"skill-{i}/SKILL.md", GOOD.format(name=f"skill-{i}"))
    write(outside, "skill-1/SKILL.md", DANGLING.format(name="skill-1"))
    write(repo, ".gatesrc", f"GOH_MAX_LINES=500\nGOH_SKILLS_CORPUS=1\nGOH_SKILLS_ROOT={outside}\n")
    commit_all(repo)

    staged = _run(runner, goh, repo, "--staged")
    assert staged.returncode == 0, staged.stdout + staged.stderr
    assert "outside this repo" in staged.stderr and "NOT checked" in staged.stderr

    full = _run(runner, goh, repo, "--full")
    assert full.returncode == 1, full.stdout + full.stderr
    assert "no-such-skill" in full.stdout + full.stderr


@RUNNERS
def test_the_index_snapshot_is_removed_after_the_run(runner, repo, goh, tmp_path):
    _corpus_repo(repo, "")
    scratch = tmp_path / "tmpdir"
    scratch.mkdir()
    r = _run(runner, goh, repo, "--staged", TMPDIR=str(scratch))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "skills corpus" in r.stdout
    leftovers = [p.name for p in scratch.iterdir() if p.name.startswith("goh-index")]
    assert leftovers == []
