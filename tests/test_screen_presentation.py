"""check_no_screen_presentation.py + lib/headless_env.sh — contract tests.

The static half is proven against fixture trees under
tests/fixtures/screen_presentation/ (copied into throwaway git repos so the
--staged/index path is exercised too). The runtime half is exercised with a
real bash that sources the real lib.
"""

import shutil
import subprocess

from conftest import REPO_ROOT, commit_all, run_check, write

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "screen_presentation"
LIB = REPO_ROOT / "lib" / "headless_env.sh"


def seed(repo, monkeypatch_dir=None):
    """Copy the screen-presentation fixtures into `repo` and commit."""
    dest = repo / "tests_src"
    shutil.copytree(FIXTURES, dest)
    commit_all(repo)
    return dest


# ---- static half: violations -------------------------------------------------


def test_swift_orderfront_fails(repo):
    src = seed(repo)
    r = run_check(repo, "checks/check_no_screen_presentation.py", str(src))
    assert r.returncode == 1
    assert f"{src.name}/Bad.swift:" in r.stderr
    assert r.stdout == "" or "OK" not in r.stdout


def test_objc_make_key_and_order_front_fails(repo):
    src = seed(repo)
    r = run_check(repo, "checks/check_no_screen_presentation.py", str(src))
    assert r.returncode == 1
    assert "bad.m:2" in r.stderr


def test_pyautogui_fails_without_any_subprocess(repo):
    src = seed(repo)
    r = run_check(repo, "checks/check_no_screen_presentation.py", str(src))
    assert r.returncode == 1
    assert "pyautogui" in r.stderr


def test_py_live_command_via_subprocess_fails(repo):
    src = seed(repo)
    r = run_check(repo, "checks/check_no_screen_presentation.py", str(src))
    assert r.returncode == 1
    assert "bad_subprocess.py:3" in r.stderr


# ---- static half: exemptions -------------------------------------------------


def test_screen_ok_marker_exempts(repo):
    src = seed(repo)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", str(src / "Marked.swift")
    )
    assert r.returncode == 0, r.stderr


def test_py_marker_exempts(repo):
    src = seed(repo)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", str(src / "marked_live.py")
    )
    assert r.returncode == 0, r.stderr


def test_py_headless_guard_exempts_file(repo):
    src = seed(repo)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", str(src / "guarded.py")
    )
    assert r.returncode == 0, r.stderr


def test_prose_mention_is_not_a_violation(repo):
    # calibrate-the-instrument: a gate that flags its own documentation stops
    # being read. Prose + no execution must stay green.
    src = seed(repo)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", str(src / "prose_only.py")
    )
    assert r.returncode == 0, r.stderr


def test_clean_repo_passes(repo):
    write(repo, "tests/test_math.py", "def test_add():\n    assert 1 + 1 == 2\n")
    write(repo, "src/app.swift", "let x = 1\n")  # non-test target scanned by scope only
    commit_all(repo)
    r = run_check(repo, "checks/check_no_screen_presentation.py", "--scope", "tests/*")
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_scope_mode_finds_violation(repo):
    write(repo, "tests/Bad.swift", "window.orderFrontRegardless()\n")
    commit_all(repo)
    r = run_check(repo, "checks/check_no_screen_presentation.py", "--scope", "tests/*")
    assert r.returncode == 1
    assert "tests/Bad.swift:1" in r.stderr


def test_staged_scope_uses_index_content(repo):
    p = write(repo, "tests/Clean.swift", "let x = 1\n")
    stage_it = subprocess.run(["git", "-C", str(repo), "add", "tests/Clean.swift"])
    assert stage_it.returncode == 0
    # index holds clean content; dirtying the worktree must not matter
    p.write_text("window.makeKeyAndOrderFront(nil)\n", encoding="utf-8")
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", "--staged", "--scope", "tests/*"
    )
    assert r.returncode == 0, r.stderr
    # ...and staging the violation DOES fail via the same scope
    subprocess.run(["git", "-C", str(repo), "add", "tests/Clean.swift"], check=True)
    r = run_check(
        repo, "checks/check_no_screen_presentation.py", "--staged", "--scope", "tests/*"
    )
    assert r.returncode == 1
    assert "tests/Clean.swift:1" in r.stderr


def test_no_targets_is_usage_error(repo):
    r = run_check(repo, "checks/check_no_screen_presentation.py")
    assert r.returncode == 2


# ---- runtime half: lib/headless_env.sh ----------------------------------------


def bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", "-c", script], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}
    )


SRC = f'. "{LIB}"'


def test_headless_env_emits_assignment():
    r = bash(f"{SRC}; headless_env")
    assert r.returncode == 0
    assert "GOH_HEADLESS=1" in r.stdout


def test_headless_env_composes_with_env():
    r = bash(f"{SRC}; env $(headless_env) printenv GOH_HEADLESS")
    assert r.returncode == 0 and r.stdout.strip() == "1"


def test_require_live_refuses_with_distinct_exit_code():
    for truthy in ("1", "yes", "true", "on"):
        r = bash(f"{SRC}; GOH_HEADLESS={truthy} headless_require_live qa/sweep")
        assert r.returncode == 3, (truthy, r.returncode)
        assert "refusing" in r.stderr


def test_require_live_allows_attended_run():
    for falsey in ("0", "false", "no", "off"):
        r = bash(f"{SRC}; GOH_HEADLESS={falsey} headless_require_live qa/sweep")
        assert r.returncode == 0, (falsey, r.stderr)
    r = bash(f"{SRC}; unset GOH_HEADLESS; headless_require_live qa/sweep")
    assert r.returncode == 0


def test_enforced_predicate_matches_contract():
    script = (
        f"{SRC}; "
        'headless_enforced || echo U1; '
        'GOH_HEADLESS=0 headless_enforced || echo F1; '
        'GOH_HEADLESS=yes headless_enforced && echo E2'
    )
    r = bash(script)
    assert r.returncode == 0
    assert "U1" in r.stdout and "F1" in r.stdout and "E2" in r.stdout
