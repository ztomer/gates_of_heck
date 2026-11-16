"""lib/loaded_verdict.py: the loaded suite leaves the ship (GOH.SHIPRELREUSE R2).

Each land starts the suite under load on the tip it landed and records the verdict by TREE; the ship
requires a green one for its tip: it waits while one runs, and refuses a red, died or missing one,
saying which. Calibrated both ways: a green command records green, a failing one red, and the ship
kit with a red or missing verdict never reaches its land.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import GiveUp, patience

LIB = REPO_ROOT / "lib" / "loaded_verdict.py"
KIT = REPO_ROOT / "tools" / "release_goh.sh"
sys.path.insert(0, str(REPO_ROOT / "lib"))
import loaded_verdict  # noqa: E402


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(drop_git=True, GOH_RUN_DIR=str(repo.parent / "run"))
    return subprocess.run([sys.executable, str(LIB), *args, "--repo", str(repo), "--sha", "HEAD"],
                          env=env, capture_output=True, text=True, timeout=patience(60),
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def _until_settled(repo: Path, cmd: str) -> str:
    path = loaded_verdict.record_path(repo, "HEAD", cmd)
    give_up = GiveUp(60)
    while not give_up.passed():
        now = loaded_verdict.state(loaded_verdict.read(path))
        if now not in {"running", "missing"}:
            return now
        time.sleep(0.2)
    return loaded_verdict.state(loaded_verdict.read(path))


def test_a_passing_run_records_green_by_tree_and_the_ship_proceeds(repo: Path) -> None:
    cmd = "test -f README.md"
    assert _cli(repo, "start", "--cmd", cmd).returncode == 0
    assert _until_settled(repo, cmd) == "green"
    rec = loaded_verdict.read(loaded_verdict.record_path(repo, "HEAD", cmd))
    assert rec and rec["tree"] == git(repo, "rev-parse", "HEAD^{tree}").strip()
    r = _cli(repo, "wait", "--max", "5", "--cmd", cmd)
    assert r.returncode == 0 and "green" in r.stdout, r.stdout + r.stderr
    assert len(git(repo, "worktree", "list").strip().splitlines()) == 1, "the run left a worktree"


def test_a_failing_run_records_red_and_the_ship_refuses_naming_it(repo: Path) -> None:
    assert _cli(repo, "start", "--cmd", "exit 3").returncode == 0
    assert _until_settled(repo, "exit 3") == "red"
    r = _cli(repo, "wait", "--max", "5", "--cmd", "exit 3")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "red (exit 3)" in r.stdout, r.stdout


def test_a_missing_verdict_refuses_with_the_way_on(repo: Path) -> None:
    r = _cli(repo, "wait", "--max", "5")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no loaded verdict" in r.stdout and "start --repo" in r.stdout, r.stdout


def test_a_running_verdict_is_waited_for(repo: Path) -> None:
    assert _cli(repo, "start", "--cmd", "sleep 2").returncode == 0
    r = _cli(repo, "wait", "--max", "60", "--cmd", "sleep 2")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "running" in r.stdout and "green" in r.stdout, r.stdout


def test_a_running_record_whose_runner_is_gone_reads_died(repo: Path) -> None:
    path = loaded_verdict.record_path(repo, "HEAD")
    gone = subprocess.Popen(["true"], stdin=subprocess.DEVNULL)
    gone.wait()
    loaded_verdict.write(path, {"state": "running", "pid": gone.pid, "log": "x"})
    r = _cli(repo, "wait", "--max", "5")
    assert r.returncode == 1 and "died" in r.stdout, r.stdout


def test_the_ship_kit_reads_the_verdict_and_never_runs_the_suite(repo: Path) -> None:
    """A red verdict for the tip refuses the release before its land; the kit no longer starts the
    loaded suite itself (it would be 485-511 s of the release step)."""
    called = repo.parent / "called.txt"
    tools = repo / "tools"
    tools.mkdir()
    for name in ("loaded_suite.sh", "land.sh"):
        (tools / name).write_text(f'#!/bin/bash\necho {name} >>"{called}"\nexit 0\n')
        (tools / name).chmod(0o755)
    (repo / "CHANGELOG.md").write_text(
        "# CHANGELOG\n\n## Unreleased\n\n* **Two.** b\n\n## v0.1.0 -- first\n\n* **One.** a\n"
    )
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "feat: x")
    path = loaded_verdict.record_path(repo, "HEAD")
    loaded_verdict.write(path, {"state": "red", "rc": 9, "log": "the-log"})
    env, bound = hermetic_env(drop_git=True), patience(120)
    r = subprocess.run(["bash", str(KIT), "0.2.0"], cwd=repo, env=env, capture_output=True,
                       text=True, timeout=bound, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode != 0, r.stdout + r.stderr
    assert "red (exit 9)" in r.stdout + r.stderr, r.stdout + r.stderr
    assert not called.exists(), called.read_text()
    loaded_verdict.write(path, {"state": "green", "rc": 0, "ended": 0})
    r = subprocess.run(["bash", str(KIT), "0.2.0"], cwd=repo, env=env, capture_output=True,
                       text=True, timeout=bound, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert called.read_text().split() == ["land.sh"], r.stdout + r.stderr


def test_land_declares_the_run_through_gatesrc() -> None:
    rc = (REPO_ROOT / ".gatesrc").read_text()
    assert "GOH_LAND_LOADED='tools/loaded_suite.sh'" in rc
    land = (REPO_ROOT / "tools" / "land.sh").read_text()
    assert "loaded_verdict.py" in land and "GOH_LAND_LOADED" in land


def test_records_are_json(repo: Path) -> None:
    loaded_verdict.write(loaded_verdict.record_path(repo, "HEAD"), {"state": "green"})
    r = _cli(repo, "show")
    assert json.loads(r.stdout)["state"] == "green"


def test_a_green_verdict_is_no_verdict_for_another_command(repo: Path) -> None:
    """GOH.VERDICTKEY, the survey's reproduction: green under `--cmd true` answered "green already"
    for `--cmd false`, keyed by the tree alone. The key carries the command (gate_identity.py)."""
    assert _cli(repo, "start", "--cmd", "true").returncode == 0
    assert _until_settled(repo, "true") == "green"
    r = _cli(repo, "start", "--cmd", "false")
    assert "green already" not in r.stdout, r.stdout
    assert _until_settled(repo, "false") == "red"


def _commit(repo: Path, files: dict[str, str], msg: str) -> str:
    for name, text in files.items():
        (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD").strip()


def test_a_tag_created_mid_run_cannot_change_its_result(repo: Path) -> None:
    """GOH.LOADEDTAGRACE: the loaded run of release commit fc32af1a went red with 142 failures
    because v0.125.0 was tagged while it ran, and every test read a version (goh_version.sh) that
    the binary built at its start did not report. The run reads the refs it STARTED with: here the
    command reads the version, a tag lands on the very commit it runs, and it reads again."""
    started, go = repo.parent / "started", repo.parent / "go"
    ver = f"bash {REPO_ROOT / 'lib' / 'goh_version.sh'} . HEAD"
    cmd = (f'v0="$({ver})"; touch {started}; until [ -f {go} ]; do sleep 0.05; done; '
           f'[ "$({ver})" = "$v0" ] && [ "$v0" != 9.9.9 ]')  # fmt: skip
    assert _cli(repo, "start", "--cmd", cmd).returncode == 0
    give_up = GiveUp(60)
    while not started.exists() and not give_up.passed():
        time.sleep(0.05)
    assert started.exists(), "the run never started its command"
    git(repo, "tag", "v9.9.9")
    go.touch()
    assert _until_settled(repo, cmd) == "green"


def test_a_release_only_land_records_its_parents_verdict_and_runs_nothing(repo: Path) -> None:
    """A release commit changes CHANGELOG.md and changelog/ only (lib/release_gate.py's one
    definition): its land records its parent's verdict for its own tree, under the same gate
    identity, instead of ~570 s of the suite under load. The command would be red on the release
    tree, so a green record proves it never ran."""
    cmd = "test ! -e CHANGELOG.md"
    assert _cli(repo, "start", "--cmd", cmd).returncode == 0
    assert _until_settled(repo, cmd) == "green"
    parent = git(repo, "rev-parse", "HEAD").strip()
    _commit(repo, {"CHANGELOG.md": "# CHANGELOG\n"}, "release: v0.2.0")
    r = _cli(repo, "start", "--cmd", cmd)
    assert r.returncode == 0 and "reused" in r.stdout, r.stdout + r.stderr
    rec = loaded_verdict.read(loaded_verdict.record_path(repo, "HEAD", cmd))
    assert rec and rec["state"] == "green" and rec["reused_from"] == parent, rec
    assert rec["tree"] == git(repo, "rev-parse", "HEAD^{tree}").strip()


def test_any_other_diff_runs_the_suite(repo: Path) -> None:
    """Calibration: one non-release path beside the changelog, or a release-only commit whose
    parent has no verdict under this command's identity, runs the command -- red here, since the
    tree it runs on has a CHANGELOG.md."""
    cmd = "test ! -e CHANGELOG.md"
    assert _cli(repo, "start", "--cmd", cmd).returncode == 0
    assert _until_settled(repo, cmd) == "green"
    _commit(repo, {"CHANGELOG.md": "# CHANGELOG\n", "README.md": "other\n"}, "feat: y")
    r = _cli(repo, "start", "--cmd", cmd)
    assert "reused" not in r.stdout, r.stdout
    assert _until_settled(repo, cmd) == "red"
    _commit(repo, {"CHANGELOG.md": "# CHANGELOG\n\nmore\n"}, "release: v0.3.0")
    other = "test ! -e CHANGELOG.md && true"
    r = _cli(repo, "start", "--cmd", other)
    assert "reused" not in r.stdout, r.stdout
    assert _until_settled(repo, other) == "red"
