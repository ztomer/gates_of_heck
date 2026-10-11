"""`goh red-run`: a commit that adds a test names the mutation that turned it red (BACKLOG 4.11).

"Prove a new test can fail" was a rule in three files and enforced nowhere. These drive the real
binary over a real staged diff, the commit-msg hook a repo opts into with GOH_RED_RUN, and the push
gate, which asks again over the pushed range so `--no-verify` does not survive the push.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, git, hermetic_env, native_goh_path

A_TEST = "def test_it_reds() -> None:\n    assert False\n"
NAMED = "feat: x\n\nbody\n\nRed-run: the assert inverted -- the test failed\n"


def _staged(repo: Path, text: str, name: str = "tests/test_new.py") -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(repo, "add", name)


def _check(repo: Path, msg: str) -> subprocess.CompletedProcess[str]:
    path = repo.parent / "MSG"
    path.write_text(msg)
    return subprocess.run(
        [str(native_goh_path()), "red-run", str(path)], cwd=repo, capture_output=True, text=True,
        env=hermetic_env(drop_git=True),
    )  # fmt: skip


def test_a_staged_test_without_the_trailer_is_refused_naming_it(repo: Path) -> None:
    _staged(repo, A_TEST)
    r = _check(repo, "feat: x\n")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "tests/test_new.py" in r.stderr and "Red-run:" in r.stderr, r.stderr


def test_a_named_red_run_passes_and_a_hollow_one_does_not(repo: Path) -> None:
    _staged(repo, A_TEST)
    assert _check(repo, NAMED).returncode == 0
    assert _check(repo, "feat: x\n\nbody\n\nRed-run: n/a\n").returncode == 1


def test_a_commit_that_adds_no_test_needs_none(repo: Path) -> None:
    _staged(repo, "def helper() -> None:\n    pass\n", "src/helper.py")
    assert _check(repo, "feat: x\n").returncode == 0


def _range(repo: Path, *revs: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(native_goh_path()), "red-run", "--range", *revs], cwd=repo, capture_output=True,
        text=True, env=hermetic_env(drop_git=True),
    )  # fmt: skip


def test_a_range_reads_each_commit_against_its_own_diff(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD").strip()
    _staged(repo, A_TEST)
    git(repo, "commit", "-q", "--no-verify", "-m", NAMED)
    _staged(repo, A_TEST, "tests/test_other.py")
    git(repo, "commit", "-q", "--no-verify", "-m", "feat: another, unproven")
    r = _range(repo, f"{base}..HEAD")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "tests/test_other.py" in r.stderr and "tests/test_new.py" not in r.stderr, r.stderr


def test_a_range_git_refuses_is_an_error_not_a_pass(repo: Path) -> None:
    r = _range(repo, "no-such-rev..HEAD")
    assert r.returncode == 2 and "no-such-rev" in r.stderr, r.stdout + r.stderr


def _hooked(repo: Path, gatesrc: str) -> None:
    subprocess.run(["bash", str(REPO_ROOT / "install.sh"), str(repo)], check=True,
                   capture_output=True, env=hermetic_env(drop_git=True, GOH_SKIP_BUILD="1"))  # fmt: skip
    (repo / ".gatesrc").write_text(gatesrc)


def _git_commit(repo: Path, msg: str) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(drop_git=True, GOH_DIR=str(REPO_ROOT), GOH_BIN=str(native_goh_path()))
    return subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", msg],
                          capture_output=True, text=True, env=env)  # fmt: skip


def test_the_commit_msg_hook_runs_it_when_the_repo_opts_in(repo: Path) -> None:
    _hooked(repo, "GOH_RED_RUN=1\n")
    _staged(repo, A_TEST)
    r = _git_commit(repo, "feat: x")
    assert r.returncode != 0 and "Red-run:" in r.stderr, r.stderr
    assert _git_commit(repo, NAMED).returncode == 0


def test_both_rules_run_when_both_are_on(repo: Path) -> None:
    _hooked(repo, "GOH_COMMIT_CLASS=1\nGOH_RED_RUN=1\n")
    _staged(repo, A_TEST)
    r = _git_commit(repo, "fix: y\n\nbody\n\nRed-run: inverted\n")
    assert r.returncode != 0 and "Class" in r.stderr, r.stderr


def test_the_commit_msg_hook_is_inert_without_the_key(repo: Path) -> None:
    _hooked(repo, "")
    _staged(repo, A_TEST)
    assert _git_commit(repo, "feat: x").returncode == 0


def test_the_push_asks_again_so_no_verify_does_not_survive_it(tmp_path: Path) -> None:
    from test_push_gate import PUSH_GATE, _repo

    proj = _repo(tmp_path, gatesrc="GOH_RED_RUN=1\n")
    base = git(proj, "rev-parse", "HEAD").strip()
    _staged(proj, A_TEST)
    git(proj, "commit", "-q", "--no-verify", "-m", "feat: skipped the hook")
    tip = git(proj, "rev-parse", "HEAD").strip()
    env = hermetic_env(drop_git=True, GOH_DIR=str(REPO_ROOT), GOH_BIN=str(native_goh_path()),
                       GATE_REPORT=str(tmp_path / "report.txt"),
                       GOH_PUSH_LOGS=str(tmp_path / "push-logs"))  # fmt: skip
    r = subprocess.run(
        ["bash", str(PUSH_GATE), "origin", "unused-url"], cwd=proj, env=env, text=True,
        input=f"refs/heads/main {tip} refs/heads/main {base}\n", capture_output=True,
    )  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Red-run:" in r.stderr and "nothing pushed" in r.stderr, r.stderr
