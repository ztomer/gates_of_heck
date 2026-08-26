"""install.sh — real-path tests: hooks installed, wired via core.hooksPath,
and driven by REAL `git commit` (user-POV: a violating commit must be blocked
by the hook itself, not by a test calling the checker directly).

Hook protection contract: a reinstall overwrites an installed hook only when
its current bytes equal the stock copy or the recorded install-time sha256
(.githooks/.goh-installed/<name>.sha256); locally extended/replaced hooks are
refused BY NAME unless --force. Red proof: every refusal test below returned
0 (hook silently clobbered) against pre-fix install.sh.
"""

import os
import shutil
import subprocess

from conftest import EMOJI_SMILE, REPO_ROOT, commit_all, git, write


def _install(target, *args):
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "install.sh"), *args, str(target)],
        capture_output=True, text=True,
    )


def _stock(rel):
    return (REPO_ROOT / "hooks" / rel).read_text()


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


# ── hook protection ──────────────────────────────────────────────────────────


def _record(target, name):
    return target / ".githooks" / ".goh-installed" / f"{name}.sha256"


def test_stock_reinstall_updates_cleanly_and_records_hashes(repo):
    for _ in range(2):
        r = _install(repo)
        assert r.returncode == 0, r.stdout + r.stderr
    assert (repo / ".githooks" / "pre-commit").read_text() == _stock("pre-commit")
    assert (repo / ".githooks" / "pre-push").read_text() == _stock("pre-push")
    for name in ("pre-commit", "pre-push"):
        rec = _record(repo, name)
        assert rec.exists(), f"no install record for {name}"
        import hashlib
        digest = hashlib.sha256(
            (repo / ".githooks" / name).read_bytes()).hexdigest()
        assert rec.read_text().strip() == digest


def test_hand_extended_hook_is_preserved_and_named_on_reinstall(repo):
    """app_updates-style: a repo APPENDS a stage to the stock pre-push."""
    assert _install(repo).returncode == 0
    push_hook = repo / ".githooks" / "pre-push"
    extended = push_hook.read_text() + '\necho "running repo-specific checks"\n'
    push_hook.write_text(extended)
    r = _install(repo)
    assert r.returncode != 0, "install clobbered a locally extended hook"
    assert "pre-push" in (r.stdout + r.stderr), "the modified file must be named"
    assert push_hook.read_text() == extended, "extension was lost"


def test_replaced_hook_is_preserved_on_reinstall(repo):
    """monitor-style: a repo REPLACES the hook with its own script outright."""
    assert _install(repo).returncode == 0
    commit_hook = repo / ".githooks" / "pre-commit"
    custom = "#!/bin/bash\n# monitor's own gate\nexit 0\n"
    commit_hook.write_text(custom)
    r = _install(repo)
    assert r.returncode != 0, "install clobbered a replaced hook"
    assert "pre-commit" in (r.stdout + r.stderr)
    assert commit_hook.read_text() == custom


def test_force_overwrites_locally_modified_hooks(repo):
    assert _install(repo).returncode == 0
    hook = repo / ".githooks" / "pre-commit"
    hook.write_text("#!/bin/bash\nexit 0\n")
    r = _install(repo, "--force")
    assert r.returncode == 0, r.stdout + r.stderr
    assert hook.read_text() == _stock("pre-commit")
    import hashlib
    assert _record(repo, "pre-commit").read_text().strip() == hashlib.sha256(
        hook.read_bytes()).hexdigest()


def test_recorded_hash_allows_update_after_a_stock_bump(tmp_path):
    """Install v1; the GOH checkout bumps the stock hook; reinstall must
    overwrite cleanly because current bytes match OUR RECORDED hash."""
    src = tmp_path / "goh-src"
    (src / "hooks").mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "tui", src / "tui")
    shutil.copy2(REPO_ROOT / "install.sh", src / "install.sh")
    for h in ("pre-commit", "pre-push"):
        shutil.copy2(REPO_ROOT / "hooks" / h, src / "hooks" / h)

    repo = tmp_path / "proj"
    subprocess.run(["git", "-C", str(repo.parent), "init", "-q",
                    str(repo.name)], check=True)

    def install_from(source, *extra):
        return subprocess.run(
            ["/bin/bash", str(source / "install.sh"), *extra, str(repo)],
            capture_output=True, text=True,
        )

    assert install_from(src).returncode == 0
    # Stock bump upstream.
    bumped = (src / "hooks" / "pre-push").read_text() + "# v2: new guard\n"
    (src / "hooks" / "pre-push").write_text(bumped)
    r = install_from(src)
    assert r.returncode == 0, (
        f"record-matched reinstall refused: {r.stdout + r.stderr}")
    assert (repo / ".githooks" / "pre-push").read_text() == bumped
