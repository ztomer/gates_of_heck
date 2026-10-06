"""An external skills corpus is judged at its last COMMIT, not its working tree (BACKLOG C2).

This repo's push reads `~/.claude/skills` (`GOH_SKILLS_ROOT`). It read the corpus's WORKING TREE,
so another session's half-done skill edit -- uncommitted, in a different repository -- refused a
gates_of_heck release. The working tree of someone else's repo is reproducible from no commit: the
C4 class one directory over. The corpus's own pre-commit gates its commits; a push here judges what
was committed there. A corpus in no repository has no commit, so its tree is read as before.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

SKILL = "---\nname: skill-{i}\ndescription: does a thing worth triggering on.\n---\n\n# s\n\nbody\n"
BROKEN = "no frontmatter at all\n"


def _corpus(root: Path, git: bool) -> Path:
    for i in range(6):
        (root / f"skill-{i}").mkdir(parents=True)
        (root / f"skill-{i}" / "SKILL.md").write_text(SKILL.format(i=i))
    if git:
        for args in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", "-C", str(root), *args], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-qm",
                "c",
            ],
            check=True,
        )
    return root


def _repo(tmp_path: Path, corpus: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gatesrc").write_text(
        f"GOH_MAX_LINES=500\nGOH_SKILLS_CORPUS=1\nGOH_SKILLS_ROOT={corpus}\n"
    )
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    return repo


def _full(goh: Path, repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(goh), "structural", "--full"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        timeout=120,
    )


def test_an_uncommitted_edit_in_an_external_corpus_is_not_this_pushs_verdict(goh, tmp_path):
    corpus = _corpus(tmp_path / "corpus", git=True)
    (corpus / "skill-0" / "SKILL.md").write_text(BROKEN)  # another session, mid-edit
    r = _full(goh, _repo(tmp_path, corpus))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "judging its last commit" in r.stderr, r.stderr


def test_a_committed_violation_in_an_external_corpus_is_red(goh, tmp_path):
    corpus = _corpus(tmp_path / "corpus", git=True)
    (corpus / "skill-0" / "SKILL.md").write_text(BROKEN)
    subprocess.run(
        [
            "git",
            "-C",
            str(corpus),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qam",
            "b",
        ],
        check=True,
    )
    r = _full(goh, _repo(tmp_path, corpus))
    assert r.returncode == 1 and "structural: skills corpus failed" in r.stderr, r.stderr


def test_a_corpus_in_no_repository_is_read_as_it_stands(goh, tmp_path):
    corpus = _corpus(tmp_path / "corpus", git=False)
    (corpus / "skill-0" / "SKILL.md").write_text(BROKEN)
    r = _full(goh, _repo(tmp_path, corpus))
    assert r.returncode == 1 and "structural: skills corpus failed" in r.stderr, r.stderr
    assert "judging its last commit" not in r.stderr
