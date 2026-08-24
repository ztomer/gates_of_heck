"""install.sh — real-path tests: hooks installed, wired via core.hooksPath,
and driven by REAL `git commit` (user-POV: a violating commit must be blocked
by the hook itself, not by a test calling the checker directly)."""

import subprocess

from conftest import EMOJI_SMILE, REPO_ROOT, commit_all, git, write


def _git_env(repo):
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "HOME": str(repo), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    return env


def _commit(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t",
         "commit", *args],
        capture_output=True, text=True,
    )


def test_install_wires_hooks_and_starters(repo):
    r = subprocess.run(["/bin/bash", str(REPO_ROOT / "install.sh"), str(repo)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (repo / ".githooks" / "pre-commit").exists()
    assert (repo / ".githooks" / "pre-push").exists()
    assert (repo / "tools" / "gate.sh").exists()
    assert (repo / ".gatesrc").exists()
    assert git(repo, "config", "core.hooksPath").strip() == ".githooks"


def test_install_is_idempotent(repo):
    for _ in range(2):
        r = subprocess.run(["/bin/bash", str(REPO_ROOT / "install.sh"), str(repo)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    assert "GOH_MAX_LINES" in (repo / ".gatesrc").read_text()


def test_hook_blocks_violating_commit(repo):
    subprocess.run(["/bin/bash", str(REPO_ROOT / "install.sh"), str(repo)],
                   capture_output=True, text=True)
    # A .py file over no cap but with an emoji; GOH_MAX_LINES unset in starter.
    (repo / "bad.py").write_text(f"x = '{EMOJI_SMILE}'\n", encoding="utf-8")
    git(repo, "add", "bad.py")
    r = _commit(repo, "-m", "violating")
    assert r.returncode != 0, "hook let a violating commit through"
    combined = r.stdout + r.stderr
    assert "emoji" in combined.lower()
    assert "bad.py" in combined
    # The staged file must still be staged — the hook blocks, it does not eat work.
    assert "bad.py" in git(repo, "diff", "--cached", "--name-only")


def test_hook_passes_clean_commit(repo):
    subprocess.run(["/bin/bash", str(REPO_ROOT / "install.sh"), str(repo)],
                   capture_output=True, text=True)
    write(repo, "ok.py", "x = 1  # → clean\n")
    git(repo, "add", "ok.py")
    r = _commit(repo, "-m", "clean")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all structural gates passed" in (r.stdout + r.stderr)


def test_self_hosted_repo_satisfies_its_own_hook(tmp_path):
    """gates_of_heck itself must pass its own pre-commit: clone-like check by
    installing into a fresh copy of THIS repo's tree and committing."""
    import shutil
    copy = tmp_path / "goh-copy"
    shutil.copytree(REPO_ROOT, copy, ignore=shutil.ignore_patterns(".git"))
    subprocess.run(["git", "-C", str(copy), "init", "-q", "-b", "main"],
                   check=True)
    subprocess.run(["/bin/bash", str(copy / "install.sh"), str(copy)],
                   capture_output=True, text=True)
    git(copy, "add", "-A")
    # GOH_DIR points the delegation at the copy itself → true self-host.
    r = subprocess.run(
        ["git", "-C", str(copy), "-c", "user.name=t", "-c", "user.email=t",
         "commit", "-m", "self-host"],
        capture_output=True, text=True,
        env={"GOH_DIR": str(copy), "HOME": str(tmp_path),
             "PATH": "/usr/bin:/bin:/opt/homebrew/bin"},
    )
    assert r.returncode == 0, r.stdout + r.stderr
