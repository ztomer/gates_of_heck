"""install.sh never silently bypasses hooks another manager owns.

zinc, 2026-10-06: its gates were pre-commit-framework hooks in `.git/hooks`
(`.pre-commit-config.yaml`). install.sh set `core.hooksPath=.githooks`, which made git
stop running every one of them, and wrote a starter gate with no layers -- a repo that
went from gated to ungated while the install said "installed". Now an install that would
bypass active hooks refuses BEFORE it writes anything, names what it found, and proceeds
only with `--replace-hooks`.
"""

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, git, hermetic_env


def _install(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "install.sh"), *args, str(target)],
        capture_output=True, text=True, env=hermetic_env(drop_git=True, GOH_SKIP_BUILD="1"), stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip


def _untouched(repo: Path) -> None:
    for rel in (".githooks", "tools/gate.sh", ".gatesrc"):
        assert not (repo / rel).exists(), f"a refused install wrote {rel}"


def _hooks_path(repo: Path) -> str:
    r = subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    return r.stdout.strip()


def test_an_active_hook_in_git_hooks_is_refused_and_nothing_is_written(repo: Path) -> None:
    hook = Path(git(repo, "rev-parse", "--git-common-dir").strip())
    hook = (hook if hook.is_absolute() else repo / hook) / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    r = _install(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "pre-commit" in r.stderr and "--replace-hooks" in r.stderr, r.stderr
    _untouched(repo)
    assert _hooks_path(repo) == ""


def test_a_pre_commit_framework_config_is_refused(repo: Path) -> None:
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n")
    r = _install(repo)
    assert r.returncode == 1 and ".pre-commit-config.yaml" in r.stderr, r.stderr
    _untouched(repo)


def test_another_hooks_path_is_refused(repo: Path) -> None:
    git(repo, "config", "core.hooksPath", ".husky")
    r = _install(repo)
    assert r.returncode == 1 and ".husky" in r.stderr, r.stderr
    _untouched(repo)
    assert _hooks_path(repo) == ".husky"


def test_replace_hooks_takes_them_over_and_says_so(repo: Path) -> None:
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n")
    r = _install(repo, "--replace-hooks")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _hooks_path(repo) == ".githooks"
    assert ".pre-commit-config.yaml" in r.stdout + r.stderr  # what it replaced is named


def test_sample_hooks_and_our_own_install_are_not_foreign(repo: Path) -> None:
    assert _install(repo).returncode == 0  # git's *.sample hooks are inert
    r = _install(repo)  # a reinstall over our own .githooks
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_absolute_hooks_path_to_our_own_githooks_is_not_foreign(repo: Path) -> None:
    """routines, 2026-10-14: core.hooksPath held the ABSOLUTE spelling of its own .githooks,
    which the refusal read as another manager's and blocked the 0.27.0 reinstall. A path is
    foreign by WHERE it points, not by how it is spelled; the take-over still rewrites it."""
    assert _install(repo).returncode == 0
    git(repo, "config", "core.hooksPath", str((repo / ".githooks").resolve()))
    r = _install(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "another manager" not in r.stderr, r.stderr


def test_an_absolute_hooks_path_elsewhere_is_still_refused(repo: Path, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere" / ".githooks"
    other.mkdir(parents=True)
    git(repo, "config", "core.hooksPath", str(other))
    r = _install(repo)
    assert r.returncode == 1 and str(other) in r.stderr, r.stderr
    _untouched(repo)
