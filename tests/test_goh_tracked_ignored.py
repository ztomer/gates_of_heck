"""`goh structural`'s tracked-but-ignored step: a tracked file the repo's own `.gitignore` matches.

The rules and the tree disagree, and one of them is wrong: either the rule is stale (the file is
a maintained tool someone listed as "local only") or the file should never have been committed (a
crash log under an ignored build directory). Found 2026-10-08 in koffee_big, which held three:
`build.sh` and `run.sh` under a stale rule, and a Kotlin daemon error log. Only the in-tree
`.gitignore` files count: `.git/info/exclude` and `core.excludesFile` are one machine's, and a
verdict that changes from machine to machine is not a gate.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from _fast_git import fast_init
from conftest import hermetic_env

ROOT = Path(__file__).resolve().parents[1]
LABEL = "no tracked file its .gitignore ignores"
FAIL_RE = re.compile(r"structural: (.+) failed")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def make_repo(tmp_path: Path, files: dict[str, str], forced: tuple[str, ...] = ()) -> Path:
    """A repo with `files` staged, and `forced` staged past the ignore rules (`git add -f`)."""
    repo = tmp_path / "repo"
    fast_init(repo)
    for name, content in files.items():
        dest = repo / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    _git(repo, "add", "-A")
    for name in forced:
        _git(repo, "add", "-f", name)
    return repo


def structural(goh: Path, repo: Path, staged: bool = False) -> subprocess.CompletedProcess:
    env = hermetic_env(GOH_DIR=str(ROOT))
    cmd = [str(goh), "structural", *(["--staged"] if staged else [])]
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, env=env, check=False)


def failing(run: subprocess.CompletedProcess) -> str | None:
    m = FAIL_RE.search(run.stdout + run.stderr)
    return m.group(1) if m else None


def committed(repo: Path) -> None:
    _git(
        repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "fixture", "--no-verify"
    )


IGNORE_MD = {".gitignore": "*.md\n", "a.py": "x = 1\n", "AGENTS.md": "# agents\n"}


def test_a_clean_repo_runs_the_step_and_passes(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {".gitignore": "*.log\n", "a.py": "x = 1\n"})
    run = structural(goh, repo)
    assert run.returncode == 0, run.stdout + run.stderr
    assert f"✓ {LABEL}" in run.stdout, run.stdout


def test_a_forced_file_under_a_rule_is_red_and_names_the_rule(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, IGNORE_MD, forced=("AGENTS.md",))
    run = structural(goh, repo)
    out = run.stdout + run.stderr
    assert run.returncode != 0 and failing(run) == LABEL, out
    assert "AGENTS.md" in out and ".gitignore:1" in out and "*.md" in out, out
    assert "git rm --cached" in out and "!AGENTS.md" in out, out


def test_a_negation_says_the_exception_is_meant(goh: Path, tmp_path: Path) -> None:
    files = {**IGNORE_MD, ".gitignore": "*.md\n!AGENTS.md\n"}
    repo = make_repo(tmp_path, files)
    run = structural(goh, repo)
    assert run.returncode == 0, run.stdout + run.stderr


def test_a_nested_gitignore_is_named(goh: Path, tmp_path: Path) -> None:
    files = {"a.py": "x = 1\n", "sub/.gitignore": "*.txt\n", "sub/my notes.txt": "n\n"}
    repo = make_repo(tmp_path, files, forced=("sub/my notes.txt",))
    run = structural(goh, repo)
    out = run.stdout + run.stderr
    assert failing(run) == LABEL, out
    assert "sub/my notes.txt" in out and "sub/.gitignore:1" in out, out


def test_machine_local_rules_do_not_count(goh: Path, tmp_path: Path) -> None:
    """One machine's excludes would make the verdict depend on whose machine ran it."""
    repo = make_repo(tmp_path, {"a.py": "x = 1\n", "b.py": "y = 2\n"})
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("a.py\n")
    global_excludes = tmp_path / "global-excludes"
    global_excludes.write_text("b.py\n")
    _git(repo, "config", "core.excludesFile", str(global_excludes))
    run = structural(goh, repo)
    assert run.returncode == 0, run.stdout + run.stderr


def test_staged_judges_only_what_the_commit_adds(goh: Path, tmp_path: Path) -> None:
    """A disagreement already committed is the full gate's to report: a pre-commit that went red
    on it would block every unrelated commit in the repo until someone fixed it."""
    repo = make_repo(tmp_path, IGNORE_MD, forced=("AGENTS.md",))
    committed(repo)
    (repo / "a.py").write_text("x = 2\n")
    _git(repo, "add", "a.py")
    run = structural(goh, repo, staged=True)
    assert run.returncode == 0, run.stdout + run.stderr


def test_staged_forcing_a_file_past_its_rule_is_red(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {".gitignore": "*.md\n", "a.py": "x = 1\n"})
    committed(repo)
    (repo / "NOTES.md").write_text("# notes\n")
    _git(repo, "add", "-f", "NOTES.md")
    run = structural(goh, repo, staged=True)
    out = run.stdout + run.stderr
    assert failing(run) == LABEL and "NOTES.md" in out, out


def test_staged_a_rule_that_newly_ignores_a_tracked_file_is_red(goh: Path, tmp_path: Path) -> None:
    """The koffee_big shape from the other side: the rule arrives after the file."""
    repo = make_repo(tmp_path, {".gitignore": "*.log\n", "a.py": "x = 1\n", "run.sh": "true\n"})
    committed(repo)
    (repo / ".gitignore").write_text("*.log\nrun.sh\n")
    _git(repo, "add", ".gitignore")
    run = structural(goh, repo, staged=True)
    out = run.stdout + run.stderr
    assert failing(run) == LABEL and "run.sh" in out and ".gitignore:2" in out, out
