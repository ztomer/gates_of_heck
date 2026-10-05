"""push_gate.sh -- a run owns ONE directory, leaves nothing behind, and reaps what a killed run left.

Measured 2026-10-05 in `~/.cache/goh/push/`: one export worktree (`MA4NUA`, detached at v0.15.1,
three days old) and 230 `*.out` files going back two weeks. Two defects, one class -- *a gate run
leaves state outside anything that removes it*:

  * The worktree sat directly in the shared export root, so anything the gate wrote BESIDE its tree
    landed in the shared root and outlived the run. Finance's `tests/test_repos.sh` writes
    `"$HERE.out"` -- a sibling of the repo root -- which in an export is the shared root.
  * The header promised "a worktree left behind by a SIGKILL is pruned by the next run", and
    `git worktree prune` only forgets a worktree whose DIRECTORY is gone. A killed run's directory
    is not gone; that is the leak. Nothing removed it.

So a run now owns `<export root>/<run>/`, stamped with its owner (pid + start time). Cleanup removes
the whole run directory and NAMES anything the gate wrote outside its tree; the next run removes any
run directory whose owner is dead.
"""

import os
import subprocess
from pathlib import Path

from test_push_gate import GATE, PUSH_GATE, REPO_ROOT, _git, _repo

LITTER_GATE = GATE.replace("exit 0\n", 'printf x > "../$(basename "$PWD").out"\nexit 0\n')


def _push(repo: Path, tmp_path: Path, export_root: Path) -> subprocess.CompletedProcess:
    sha = _git(repo, "rev-parse", "HEAD")
    env = dict(
        os.environ,
        GATE_REPORT=str(tmp_path / "report.txt"),
        GOH_DIR=str(REPO_ROOT),
        GOH_PUSH_LOGS=str(tmp_path / "push-logs"),
        GOH_PUSH_WORKTREES=str(export_root),
    )
    return subprocess.run(
        ["bash", str(PUSH_GATE)],
        cwd=repo,
        env=env,
        text=True,
        input=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n",
        capture_output=True,
        timeout=60,
    )


def _stamp(pid: int) -> str:
    start = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout
    return f"{pid} {' '.join(start.split())}\n"


def _dead_pid() -> int:
    proc = subprocess.Popen(["/usr/bin/true"])
    proc.wait()
    return proc.pid


def _abandoned_run(repo: Path, export_root: Path, name: str, owner: str) -> Path:
    """A run directory exactly as a SIGKILLed gate leaves it: stamped, with a registered worktree."""
    run = export_root / name
    run.mkdir(parents=True)
    (run / ".owner").write_text(owner)
    _git(repo, "worktree", "add", "--detach", "-q", str(run / "repo"), "HEAD")
    return run


def test_what_a_gate_writes_beside_its_tree_is_removed_and_named(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tools" / "gate.sh").write_text(LITTER_GATE)
    _git(repo, "commit", "-qam", "a gate that writes beside its tree")
    export_root = tmp_path / "exports"
    got = _push(repo, tmp_path, export_root)
    assert got.returncode == 0, got.stdout + got.stderr
    assert not list(export_root.iterdir()), f"the run left: {list(export_root.rglob('*'))}"
    assert "outside its tree" in got.stdout + got.stderr and "repo.out" in got.stdout + got.stderr


def test_a_red_run_leaves_no_run_directory(tmp_path):
    repo = _repo(tmp_path)
    (repo / "fail.flag").write_text("")
    _git(repo, "add", "fail.flag")
    _git(repo, "commit", "-q", "-m", "red")
    export_root = tmp_path / "exports"
    got = _push(repo, tmp_path, export_root)
    assert got.returncode == 1, got.stdout + got.stderr
    assert not list(export_root.iterdir()), list(export_root.rglob("*"))


def test_a_run_whose_owner_is_dead_is_reaped_by_the_next(tmp_path):
    repo = _repo(tmp_path)
    export_root = tmp_path / "exports"
    dead = _abandoned_run(repo, export_root, "KILLED", _stamp(_dead_pid()))
    got = _push(repo, tmp_path, export_root)
    assert got.returncode == 0, got.stdout + got.stderr
    assert not dead.exists(), "a killed run's directory survived the next run"
    assert _git(repo, "worktree", "list").count("\n") == 0, _git(repo, "worktree", "list")


def test_a_reused_pid_is_not_mistaken_for_the_owner(tmp_path):
    """A pid alone is a rumour: the owner died and the number came back as someone else. The start
    time is what makes the stamp an identity."""
    repo = _repo(tmp_path)
    export_root = tmp_path / "exports"
    dead = _abandoned_run(repo, export_root, "REUSED", f"{os.getpid()} Thu Jan  1 00:00:00 1970\n")
    assert _push(repo, tmp_path, export_root).returncode == 0
    assert not dead.exists()


def test_a_live_run_is_never_reaped(tmp_path):
    """The other direction: two pushes at once must not delete each other's tree."""
    repo = _repo(tmp_path)
    export_root = tmp_path / "exports"
    live = _abandoned_run(repo, export_root, "LIVE", _stamp(os.getpid()))
    got = _push(repo, tmp_path, export_root)
    assert got.returncode == 0, got.stdout + got.stderr
    assert (live / "repo").is_dir(), "a live run's tree was deleted under it"


# ── a STABLE export path per repo ─────────────────────────────────────────────────────────────
# ~/.cargo/config.toml keys the build directory by `{workspace-path-hash}`, and the export used to
# land at a random path: every push built every dependency cold into a brand-new build-dir and left
# it behind. Measured 2026-10-05: 24 such dirs, 30 GB, in three days.


def _cwds(tmp_path: Path) -> list[str]:
    report = (tmp_path / "report.txt").read_text()
    return [line.split("=", 1)[1] for line in report.splitlines() if line.startswith("cwd=")]


def test_the_same_repo_exports_to_the_same_path_every_time(tmp_path):
    repo = _repo(tmp_path)
    export_root = tmp_path / "exports"
    for _ in range(2):
        got = _push(repo, tmp_path, export_root)
        assert got.returncode == 0, got.stdout + got.stderr
    first, second = _cwds(tmp_path)
    assert first == second, f"two pushes of one repo exported to two paths: {first} vs {second}"


def test_a_concurrent_push_of_the_same_repo_takes_a_private_path_and_build_dir(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tools" / "gate.sh").write_text(
        GATE.replace("exit 0\n", 'printf "build=%s\\n" "${CARGO_BUILD_BUILD_DIR:-}" >> "$out"\nexit 0\n')
    )
    _git(repo, "commit", "-qam", "report the build dir")
    export_root = tmp_path / "exports"
    assert _push(repo, tmp_path, export_root).returncode == 0
    stable = Path(_cwds(tmp_path)[0]).parent
    # The stable path is now held by a LIVE run.
    _abandoned_run(repo, export_root, stable.name, _stamp(os.getpid()))
    got = _push(repo, tmp_path, export_root)
    assert got.returncode == 0, got.stdout + got.stderr
    second = _cwds(tmp_path)[-1]
    assert Path(second).parent != stable, "a live run's export path was taken"
    build = (tmp_path / "report.txt").read_text().splitlines()[-1].split("=", 1)[1]
    assert build.startswith(str(Path(second).parent)), f"the fallback build-dir outlives its run: {build}"
