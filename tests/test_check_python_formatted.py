"""checks/check_python_formatted.py — the repo's Python is ruff-formatted.

The class: a Python tree with no formatter has its shape decided by whichever
hand last edited each file, and the damage that causes (a comment merged onto a
`continue`, a statement de-indented out of its loop) parses and reads fine, so
only a tool can catch it.

So the gate must go RED on a mis-formatted file, GREEN on a formatted one, and
RED-with-a-reason when ruff is missing — a formatter that is not installed is a
missing gate, not a pass. It must also not FORMAT: the checker answers a
question, so "the gate went red" and "somebody edited 100 files" stay different
events. And the answer must come from the repo's own ruff settings, which means
the settings file that applies is the one at the repo root — a formatter whose
verdict depends on the directory it was launched from is not a gate.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, commit_all, stage, write
from tier_kit import both_tiers, run_tiered  # noqa: F401  # both_tiers: a fixture
from timing_bounds import assert_sooner

CHECK = "checks/check_python_formatted.py"

pytestmark = pytest.mark.usefixtures("both_tiers")


def pf(repo, *args):
    """The checker over `repo`, on the current tier (Python, or `goh python-formatted`)."""
    return run_tiered(repo, CHECK, "python-formatted", *args)


BAD = "x = {  'a':1,'b':2 }\n"
GOOD = 'x = {"a": 1, "b": 2}\n'

needs_ruff = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff is not installed")


@needs_ruff
def test_misformatted_file_is_red_and_formatted_is_green(repo):
    write(repo, "tools/bad.py", BAD)
    write(repo, "tools/good.py", GOOD)
    bad = pf(repo, "tools")
    assert bad.returncode == 1, bad.stdout + bad.stderr
    assert "not ruff-formatted" in bad.stdout + bad.stderr

    (repo / "tools" / "bad.py").unlink()
    good = pf(repo, "tools")
    assert good.returncode == 0, good.stdout + good.stderr


@needs_ruff
def test_a_named_tree_does_not_judge_the_rest_of_the_repo(repo):
    """The tree list is a scope, not a suggestion: an unformatted file OUTSIDE it
    must not decide the answer, or the gate is only as narrow as whoever listed
    the trees — and a rename that moves a file out of the list silences it."""
    write(repo, "tools/good.py", GOOD)
    write(repo, "scratch/bad.py", BAD)
    only_tools = pf(repo, "tools")
    assert only_tools.returncode == 0, only_tools.stdout + only_tools.stderr

    whole_repo = pf(repo)
    assert whole_repo.returncode == 1, whole_repo.stdout + whole_repo.stderr


@needs_ruff
def test_missing_ruff_is_a_failure_not_a_skip(repo, monkeypatch, tmp_path):
    """A formatter that is not installed cannot check anything, and a gate that
    passes because its tool was missing has reported success it never earned.

    PATH keeps the interpreter and loses only ruff — an empty PATH would fail
    this test by hiding `python3` too, which proves nothing about the gate.
    """
    write(repo, "tools/good.py", GOOD)
    narrow = tmp_path / "bin"
    narrow.mkdir()
    (narrow / "python3").symlink_to(Path(sys.executable))
    monkeypatch.setenv("PATH", str(narrow))
    assert shutil.which("ruff") is None, (
        "ruff is still reachable; the control is not testing itself"
    )
    out = pf(repo, "tools")
    assert out.returncode == 1, out.stdout + out.stderr
    assert "ruff is not installed" in out.stdout + out.stderr


def test_selftest_gates_on_its_own_detection():
    """The calibration must run without a repo of its own — it plants both files
    in a temp directory and asks the real ruff binary about them."""
    out = subprocess.run(
        ["python3", str(REPO_ROOT / CHECK), "--selftest"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "mis-formatted file is caught" in out.stdout + out.stderr


@needs_ruff
def test_the_gate_does_not_reformat_anything(repo):
    """It answers a question. Applying the formatter is the repo's own `fmt`
    target, so a red gate and an edited tree are never the same event."""
    write(repo, "tools/bad.py", BAD)
    before = (repo / "tools" / "bad.py").read_text()
    pf(repo, "tools")
    assert (repo / "tools" / "bad.py").read_text() == before


# ── --staged: the commit's own bytes, and only its files ─────────────────────
# The v0.20.0 push was refused for two unformatted test files that pre-commit had
# let through, because the step ran at full scope only. A check over the INDEX
# blobs of the staged .py files reformats nothing, so the reason for keeping it
# out of pre-commit ("a hook that reformatted the repository") never applied.


@needs_ruff
def test_staged_judges_the_index_blob_not_the_worktree(repo):
    """Index truth: the staged bytes are what the commit will contain. A worktree
    fixed after `git add` must not launder a misformatted commit, and a worktree
    broken after `git add` must not refuse a well-formed one."""
    write(repo, "tools/a.py", BAD)
    stage(repo, "tools/a.py")
    write(repo, "tools/a.py", GOOD)
    red = pf(repo, "--staged")
    assert red.returncode == 1, red.stdout + red.stderr
    assert "tools/a.py" in red.stdout + red.stderr

    stage(repo, "tools/a.py")
    write(repo, "tools/a.py", BAD)
    green = pf(repo, "--staged")
    assert green.returncode == 0, green.stdout + green.stderr


@needs_ruff
def test_staged_does_not_judge_files_outside_the_commit(repo):
    """An unformatted file that is not part of this commit is the full-scope
    gate's business: refusing a commit over a file it does not touch is the
    pre-commit hook nobody can satisfy."""
    write(repo, "scratch/bad.py", BAD)
    commit_all(repo)
    write(repo, "tools/good.py", GOOD)
    stage(repo, "tools/good.py")
    out = pf(repo, "--staged")
    assert out.returncode == 0, out.stdout + out.stderr


@needs_ruff
def test_staged_with_no_python_is_a_named_non_run(repo):
    write(repo, "notes.md", "# notes\n")
    stage(repo, "notes.md")
    out = pf(repo, "--staged")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "not applicable" in out.stdout + out.stderr


@needs_ruff
def test_staged_honours_the_repos_own_excludes(repo):
    """ruff resolves settings from the stdin filename, but an explicitly named
    path skips its excludes unless forced: a repo that excludes a generated tree
    must not be refused over it at commit time when the full gate allows it."""
    write(repo, "pyproject.toml", '[tool.ruff]\nextend-exclude = ["gen"]\n')
    write(repo, "gen/out.py", BAD)
    stage(repo, "pyproject.toml", "gen/out.py")
    out = pf(repo, "--staged")
    assert out.returncode == 0, out.stdout + out.stderr
    full = pf(repo)
    assert full.returncode == 0, full.stdout + full.stderr


@needs_ruff
def test_staged_missing_ruff_is_a_failure_not_a_skip(repo, monkeypatch, tmp_path):
    write(repo, "tools/good.py", GOOD)
    stage(repo, "tools/good.py")
    narrow = tmp_path / "bin"
    narrow.mkdir()
    (narrow / "python3").symlink_to(Path(sys.executable))
    (narrow / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(narrow))
    out = pf(repo, "--staged")
    assert out.returncode == 1, out.stdout + out.stderr
    assert "ruff is not installed" in out.stdout + out.stderr


def test_a_hung_ruff_is_timed_out_and_its_group_killed(repo, tmp_path, goh):
    """The port spawns ruff itself, so it owns the ceiling `bounded_run` gave the delegated step:
    a ruff that never returns is cut at GOH_STEP_TIMEOUT, its process GROUP killed, and the run
    says TIMED OUT -- never a hang. (Native only: the Python checker is bounded by its caller.)"""
    import os
    import subprocess
    import time

    marker = tmp_path / "ruff-child.pid"
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "ruff").write_text(f"#!/bin/sh\nsleep 300 &\necho $! > {marker}\nwait\n")
    (fake / "ruff").chmod(0o755)
    write(repo, "tools/good.py", GOOD)
    env = dict(os.environ, PATH=f"{fake}:{os.environ['PATH']}", GOH_STEP_TIMEOUT="2")
    t0 = time.monotonic()
    r = subprocess.run(
        [str(goh), "python-formatted", "tools"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert_sooner(time.monotonic() - t0, 3, 300, "a 2 s step ceiling over a 300 s ruff")
    assert r.returncode == 1 and "TIMED OUT after 2s" in r.stderr, r.stdout + r.stderr
    pid = int(marker.read_text())
    time.sleep(0.2)
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    if alive:
        os.kill(pid, 9)
    assert not alive, "ruff's own child survived: the group was not killed"


# ── which ruff: the repo's pinned one, by the Python gates' own runner rule (GOH.RUFFPIN) ────────
# `goh python-formatted` ran whatever `ruff` was first on PATH, so the verdict depended on the shell
# it was launched from while gates/_py.sh (`goh_py_runner_choose`) judged the same tree with the
# repo's pin: a tracked uv.lock runs `uv run --frozen ruff`, a .venv runs its own, else PATH.
# Every test puts a PATH ruff that is RED beside a pinned one that is GREEN (or the reverse), so a
# gate that took the wrong ruff gives the wrong verdict -- the two can never agree by accident.


def _fake(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def _with_path_ruff(tmp_path, *, verdict: int) -> tuple[dict[str, str], Path]:
    """An env whose first PATH ruff exits `verdict`, and the log every fake appends its argv to."""
    log = tmp_path / "argv.log"
    _fake(tmp_path / "pathbin" / "ruff", f'echo "PATH-RUFF $*" >> "{log}"\nexit {verdict}\n')
    env = dict(os.environ, PATH=f"{tmp_path / 'pathbin'}:{os.environ['PATH']}")
    for k in ("GOH_PY_RUNNER", "GOH_PYTHON_VERSION"):
        env.pop(k, None)
    return env, log


def test_a_tracked_uv_lock_runs_the_pinned_ruff_not_the_one_on_path(repo, tmp_path):
    env, log = _with_path_ruff(tmp_path, verdict=1)  # PATH's ruff would call every file bad
    _fake(tmp_path / "pathbin" / "uv", f'echo "UV $*" >> "{log}"\nexit 0\n')
    write(repo, "tools/good.py", GOOD)
    write(repo, "uv.lock", "version = 1\n")
    stage(repo, "uv.lock")
    commit_all(repo)
    r = run_tiered(repo, CHECK, "python-formatted", "tools", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    argv = log.read_text()
    assert "UV run --frozen ruff format --check" in argv and "PATH-RUFF" not in argv, argv


def test_the_pinned_ruff_is_the_one_that_fails_when_it_is_the_one_that_disagrees(repo, tmp_path):
    env, log = _with_path_ruff(tmp_path, verdict=0)  # PATH's ruff would pass everything
    _fake(tmp_path / "pathbin" / "uv", f'echo "UV $*" >> "{log}"\necho "would reformat"\nexit 1\n')
    write(repo, "tools/good.py", GOOD)
    write(repo, "uv.lock", "version = 1\n")
    stage(repo, "uv.lock")
    commit_all(repo)
    r = run_tiered(repo, CHECK, "python-formatted", "tools", env=env)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "PATH-RUFF" not in log.read_text()


def test_staged_runs_the_pinned_ruff_too(repo, tmp_path):
    env, log = _with_path_ruff(tmp_path, verdict=1)
    _fake(tmp_path / "pathbin" / "uv", f'echo "UV $*" >> "{log}"\ncat > /dev/null\nexit 0\n')
    write(repo, "uv.lock", "version = 1\n")
    stage(repo, "uv.lock")
    commit_all(repo)
    write(repo, "tools/a.py", GOOD)
    stage(repo, "tools/a.py")
    r = run_tiered(repo, CHECK, "python-formatted", "--staged", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    argv = log.read_text()
    assert "UV run --frozen ruff format --check" in argv and "PATH-RUFF" not in argv, argv


def test_a_venv_runs_its_own_ruff_when_no_lock_is_tracked(repo, tmp_path):
    env, log = _with_path_ruff(tmp_path, verdict=1)
    _fake(repo / ".venv" / "bin" / "python", f'echo "VENV $*" >> "{log}"\nexit 0\n')
    write(repo, "tools/good.py", GOOD)
    r = run_tiered(repo, CHECK, "python-formatted", "tools", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    argv = log.read_text()
    assert "VENV -m ruff format --check" in argv and "PATH-RUFF" not in argv, argv


def test_a_tracked_lock_outranks_a_venv(repo, tmp_path):
    env, log = _with_path_ruff(tmp_path, verdict=1)
    _fake(repo / ".venv" / "bin" / "python", f'echo "VENV $*" >> "{log}"\nexit 1\n')
    _fake(tmp_path / "pathbin" / "uv", f'echo "UV $*" >> "{log}"\nexit 0\n')
    write(repo, "tools/good.py", GOOD)
    write(repo, "uv.lock", "version = 1\n")
    stage(repo, "uv.lock")
    commit_all(repo)
    r = run_tiered(repo, CHECK, "python-formatted", "tools", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "VENV" not in log.read_text()


def test_goh_py_runner_names_the_ruff_and_the_python_version_reaches_uv(repo, tmp_path):
    env, log = _with_path_ruff(tmp_path, verdict=1)
    _fake(tmp_path / "pathbin" / "uv", f'echo "UV $*" >> "{log}"\nexit 0\n')
    write(repo, "tools/good.py", GOOD)
    write(repo, "uv.lock", "version = 1\n")
    stage(repo, "uv.lock")
    commit_all(repo)
    pinned = run_tiered(
        repo, CHECK, "python-formatted", "tools", env=dict(env, GOH_PYTHON_VERSION="3.14")
    )
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    assert "UV run --frozen --python 3.14 ruff format" in log.read_text()
    runner = _fake(tmp_path / "myrunner", f'echo "RUNNER $*" >> "{log}"\nexit 0\n')
    named = run_tiered(
        repo, CHECK, "python-formatted", "tools", env=dict(env, GOH_PY_RUNNER=str(runner))
    )
    assert named.returncode == 0, named.stdout + named.stderr
    assert "RUNNER ruff format --check" in log.read_text()


def test_a_tracked_lock_with_no_uv_is_a_named_refusal_not_the_path_ruff(repo, tmp_path):
    env, log = _with_path_ruff(tmp_path, verdict=0)
    write(repo, "tools/good.py", GOOD)
    write(repo, "uv.lock", "version = 1\n")
    stage(repo, "uv.lock")
    commit_all(repo)
    narrow = tmp_path / "narrow"
    narrow.mkdir()
    (narrow / "git").symlink_to(shutil.which("git"))
    (narrow / "ruff").symlink_to(tmp_path / "pathbin" / "ruff")
    r = run_tiered(repo, CHECK, "python-formatted", "tools", env=dict(env, PATH=str(narrow)))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "uv.lock" in r.stdout + r.stderr and "uv" in r.stdout + r.stderr
    assert not log.exists() or "PATH-RUFF" not in log.read_text()
