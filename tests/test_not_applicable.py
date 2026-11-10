"""NOT APPLICABLE: a step that needs the owner's estate, on a machine that has none (CI.PUBLIC2).

goh's first hosted run (42600807333) went red on three steps no commit could fix: the R3 corpus
probe and the release canary measure the owner's estate, and a GitHub-hosted runner has none. Each
now probes its prerequisite and, ON A HOSTED RUN ONLY, exits 78 with a NOT APPLICABLE line -- not a
pass and not a failure. Every test here runs BOTH directions, because the failure this guards is a
probe that could silently pass on the owner's machine, the judge of record: there the same absent
estate is red, as it always was, and local_ci.sh refuses a step that says 78.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import not_applicable

ESTATE = REPO_ROOT / "checks" / "check_estate_corpus.py"
CANARY = REPO_ROOT / "scripts" / "release_canary.py"
LOCAL_CI = REPO_ROOT / "gates" / "local_ci.sh"
MODES = [pytest.param("true", id="hosted"), pytest.param("", id="owner-machine")]


def _env(hosted: str, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_ACTIONS"}
    if hosted:
        env["GITHUB_ACTIONS"] = hosted
    env.update(extra)
    return env


def _run(argv: list[str], env: dict[str, str], cwd: Path | None = None):
    got = subprocess.run(
        argv, capture_output=True, text=True, env=env, cwd=cwd, timeout=300,
        stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    return got.returncode, got.stdout + got.stderr


def test_only_a_github_actions_run_is_hosted():
    assert not_applicable.hosted({"GITHUB_ACTIONS": "true"})
    for env in ({}, {"GITHUB_ACTIONS": ""}, {"GITHUB_ACTIONS": "1"}, {"CI": "true"}):
        assert not not_applicable.hosted(env), env


@pytest.mark.parametrize("hosted", MODES)
def test_the_corpus_probe_without_an_estate(tmp_path, hosted):
    rc, out = _run([sys.executable, str(ESTATE), "--probe"], _env(hosted, HOME=str(tmp_path)))
    if hosted:
        assert rc == not_applicable.EXIT, out
        assert not_applicable.MARK in out and "a corpus below the floor is refused" in out, out
    else:
        assert rc == 1 and not_applicable.MARK not in out, out
        assert "the first real estate entry did not verify" in out, out


@pytest.mark.parametrize("hosted", MODES)
def test_the_corpus_sweep_of_nothing(tmp_path, hosted):
    rc, out = _run([sys.executable, str(ESTATE)], _env(hosted, HOME=str(tmp_path)))
    if hosted:
        assert rc == not_applicable.EXIT and not_applicable.MARK in out, out
    else:
        assert rc == 1 and "a sweep of nothing" in out, out


@pytest.mark.parametrize("hosted", MODES)
def test_the_release_canary_without_an_estate(tmp_path, hosted):
    root, state = tmp_path / "Projects", tmp_path / "state" / "estate.json"
    root.mkdir()
    state.parent.mkdir()
    argv = [sys.executable, str(CANARY), "--root", str(root), "--state", str(state)]
    argv += ["--base-bin", "/bin/true", "--head-bin", "/bin/true", "--goh", str(REPO_ROOT)]
    rc, out = _run(argv, _env(hosted, GOH_VERDICT_DIR=str(tmp_path / "v")))
    if hosted:
        assert rc == not_applicable.EXIT and not_applicable.MARK in out, out
    else:
        assert rc == 1 and "a sweep of nothing" in out and not_applicable.MARK not in out, out


@pytest.mark.parametrize("hosted", MODES)
def test_an_existing_but_empty_registry_is_never_not_applicable(tmp_path, hosted):
    """The estate exists and holds no consumer: a real refusal, on a hosted run too."""
    root, state = tmp_path / "Projects", tmp_path / "state" / "estate.json"
    root.mkdir()
    state.parent.mkdir()
    (state.parent / "installed_repos").write_text("")
    argv = [sys.executable, str(CANARY), "--root", str(root), "--state", str(state)]
    argv += ["--base-bin", "/bin/true", "--head-bin", "/bin/true", "--goh", str(REPO_ROOT)]
    rc, out = _run(argv, _env(hosted, GOH_VERDICT_DIR=str(tmp_path / "v")))
    assert rc == 1 and not_applicable.MARK not in out, out


@pytest.mark.parametrize("hosted", MODES)
def test_local_ci_accepts_78_only_on_a_hosted_run(tmp_path, hosted):
    repo = tmp_path / "proj"
    fast_init(repo)
    (repo / "na.sh").write_text('echo "NOT APPLICABLE on this hosted runner: s needs e"\nexit 78\n')
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    env = _env(hosted, GOH_PROVEN="0", TMPDIR=str(tmp))
    rc, out = _run(["bash", str(LOCAL_CI), "--step", "bash na.sh", "--step", "true"], env, repo)
    kept = sorted(p.name for p in tmp.glob("goh-local-ci.*"))
    if hosted:
        assert rc == 0, out
        assert "[1/2] NOT APPLICABLE on this hosted runner" in out and "s needs e" in out, out
        assert "1 of 2 steps NOT APPLICABLE" in out and "all 1 step passed" in out, out
        assert kept == [], f"a green run keeps no log: {kept}"
    else:
        assert rc == 1, out
        assert "off a hosted runner" in out and "bash na.sh (exit status 78)" in out, out
        assert len(kept) == 1, f"a red run keeps its one log, where TMPDIR says: {kept}"


def test_the_suite_keeps_its_children_out_of_the_users_temp_dir():
    """A red gate keeps its log (local_ci.sh, on purpose); under the suite that log is the
    suite's, never the user's (tests/suite_tmp.py)."""
    import suite_tmp

    mine, real = os.environ["TMPDIR"], suite_tmp.real_tmp()
    assert os.path.realpath(mine) != os.path.realpath(real), (mine, real)
    assert os.path.realpath(mine).startswith(os.path.realpath(real)), (mine, real)
    assert os.environ.get(suite_tmp.MARKER) == mine
