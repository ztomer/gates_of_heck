"""A tree the lander moved runs the fast set alone when the gate measures (GOH.PRESTEPDUP).

BASETEMPSLOT and SUITEONCE's batch (2026-11-18) stacked both lanes on main, so the tip's tree was
new and tools/land.sh ran the whole pre-step on it (353 s) holding the land lock; the gate then ran
the instrumented coverage suite (665 s), which runs every test the pre-step had. Now a tree moved
after a pre-step judged the lane -- rebased under the lock, or stacked by the lander's batch, which
holds it -- runs GOH_LAND_PRE_FAST instead when lib/pre_fast.py says the gate MEASURES (a coverage
input moved since main). A gate that may reuse the set's verdict (GOH.SUITEONCE's plain path) still
gets the whole set.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, git, hermetic_env
from test_land import LAND, _main, _worktree
from timing_bounds import patience

from lib import pre_fast

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at


def _declare_on_main(repo: Path, tmp_path: Path, fast: str = "") -> tuple[Path, Path, Path]:
    """main declares the pre-step, its fast set and an external suite; the logs each one writes."""
    full, quick, seen = tmp_path / "full", tmp_path / "fast", tmp_path / "seen"
    fast = fast or f"git rev-parse HEAD >> {quick}"
    write_gatesrc(repo, f"GOH_LAND_GATE='echo gate >> {seen}'\n"
                        f"GOH_LAND_PRE='git rev-parse HEAD >> {full}'\n"
                        f"GOH_LAND_PRE_FAST='{fast}'\n"
                        "GOH_COV_RUST_EXTERNAL='./run.sh'\n")  # fmt: skip
    # A cargo workspace whose one member is under the keyed `crates` (lib/gate_identity.py rust).
    (repo / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/a"]\nresolver = "2"\n')
    (repo / "crates" / "a" / "src").mkdir(parents=True)
    (repo / "crates" / "a" / "Cargo.toml").write_text(
        '[package]\nname = "a"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    (repo / "crates" / "a" / "src" / "lib.rs").write_text("")
    git(repo, "add", ".gatesrc", "Cargo.toml", "crates")
    git(repo, "commit", "-q", "--no-verify", "-m", "declare")
    return full, quick, seen


def _commit(repo: Path, rel: str) -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text("main's\n")
    git(repo, "add", rel)
    git(repo, "commit", "-q", "--no-verify", "-m", f"main adds {rel}")


def _land(wt: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(drop_git=True)
    env.pop("GATES_OF_HECK_SUITE_RUNNER", None)
    env.update(extra)
    return subprocess.run(["bash", str(LAND)], cwd=wt, env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=patience(120), check=False)  # fmt: skip


def _runs(log: Path) -> list[str]:
    return log.read_text().split() if log.exists() else []


def _moved_during_pre(repo: Path, rel: str, full: Path, once: Path) -> str:
    """GOH_LAND_PRE's first run lands `rel` on main: the land rebases under the lock."""
    commit = f"git -C {repo} add {rel}; git -C {repo} commit -q --no-verify -m late"
    move = f"[ -e {once} ] || {{ : > {once}; echo x > {repo}/{rel}; {commit}; }}"
    return f"git rev-parse HEAD >> {full}; {move}"


def test_a_rebase_under_the_lock_of_a_lane_moving_a_coverage_input_runs_the_fast_set_alone(
    repo: Path, tmp_path: Path
) -> None:
    full, quick, seen = _declare_on_main(repo, tmp_path)
    wt = _worktree(repo, tmp_path)
    _commit(wt, "crates/lane.rs")
    pre = _moved_during_pre(repo, "late.txt", full, tmp_path / "once")
    r = _land(wt, GOH_LAND_PRE=pre)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert len(_runs(full)) == 1, out  # the lane's tree, once
    assert _runs(quick) == [_main(repo)], out  # the rebased tip: the fast set alone
    assert "the fast set alone" in out and "crates/lane.rs" in out, out
    assert seen.read_text() == "gate\n", out


def test_a_rebase_of_a_lane_moving_no_coverage_input_still_runs_the_whole_set(
    repo: Path, tmp_path: Path
) -> None:
    """The plain path: main moved a Rust input, but main's gate measured it; the lane moves none,
    so the gate may take the set's verdict (GOH.SUITEONCE) and the set runs again."""
    full, quick, _ = _declare_on_main(repo, tmp_path)
    wt = _worktree(repo, tmp_path)
    pre = _moved_during_pre(repo, "crates/late.rs", full, tmp_path / "once")
    r = _land(wt, GOH_LAND_PRE=pre)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    runs = _runs(full)
    assert len(runs) == 2 and runs[1] == _main(repo) != runs[0], out
    assert not quick.exists(), out
    assert "no coverage input moved" in out, out


def _held(repo: Path) -> dict[str, str]:
    """The land lock held by this process, as the lander's batch holds it (lib/land_lock.py)."""
    lock = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    lock = lock / "goh-land.lock"
    lock.write_text(f"landq batch test (pid {os.getpid()}, since 00:00:00)\n")
    return {"GOH_LAND_LOCK_OWNER": f"{lock} {os.getpid()}"}


def test_a_batch_stack_that_moves_a_coverage_input_runs_the_fast_set_alone(
    repo: Path, tmp_path: Path
) -> None:
    full, quick, seen = _declare_on_main(repo, tmp_path)
    wt = _worktree(repo, tmp_path)
    _commit(wt, "crates/lane.rs")
    r = _land(wt, **_held(repo))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "held for this land by its caller" in out, out
    assert not full.exists(), out
    assert _runs(quick) == [_main(repo)], out
    assert seen.read_text() == "gate\n", out


def test_a_batch_stack_with_no_coverage_input_runs_the_whole_set(
    repo: Path, tmp_path: Path
) -> None:
    full, quick, _ = _declare_on_main(repo, tmp_path)
    wt = _worktree(repo, tmp_path)
    r = _land(wt, **_held(repo))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert _runs(full) == [_main(repo)], out
    assert not quick.exists(), out


def test_a_red_fast_set_on_a_batch_stack_lands_nothing(repo: Path, tmp_path: Path) -> None:
    """A red half of a bisected batch is judged so too: the fast set red, no gate, main unmoved."""
    _, _, seen = _declare_on_main(repo, tmp_path, fast="false")
    wt = _worktree(repo, tmp_path)
    _commit(wt, "crates/lane.rs")
    before = _main(repo)
    r = _land(wt, **_held(repo))
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "the pre-land tests failed" in out, out
    assert not seen.exists() and _main(repo) == before, out


def test_a_first_pre_step_is_the_whole_set_whatever_the_gate_measures(
    repo: Path, tmp_path: Path
) -> None:
    """No pre-step judged the lane and no batch holds the lock: the set is the fail-fast."""
    full, quick, _ = _declare_on_main(repo, tmp_path)
    wt = _worktree(repo, tmp_path)
    _commit(wt, "crates/lane.rs")
    r = _land(wt)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert _runs(full) == [_main(repo)] and not quick.exists(), out


def test_the_fast_only_run_writes_a_record_no_gate_takes_for_the_set(
    tmp_path: Path, monkeypatch
) -> None:
    record = tmp_path / "record"
    monkeypatch.setenv("GOH_LAND_VERDICTS_RECORD", str(record))
    monkeypatch.setattr(at.fast_tests, "run", lambda root, judges: 0)
    monkeypatch.setattr(at, "affected", lambda root, changed: {})
    monkeypatch.setattr(at, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("set ran")))
    assert at.main(["--run", "--fast-only", "--root", str(tmp_path)]) == 0
    rec = json.loads(record.read_text())
    assert rec["passed"] == {} and "selection" not in rec and rec["identity"] is None


def test_keyed_reads_the_paths_each_identity_line_lists() -> None:
    lines = ["rust-identity v2", "inputs abc crates Cargo.toml", "scripts def gates/a.sh",
             "runner none", "tool rustc /x 1"]  # fmt: skip
    got = pre_fast.keyed(lines)
    assert {"crates", "Cargo.toml", "gates/a.sh", ".gatesrc"} <= got and "/x" not in got


def test_no_external_suite_may_reuse(tmp_path: Path) -> None:
    code, why = pre_fast.decide(tmp_path, "main", None)
    assert code == pre_fast.MAY_REUSE and "GOH_COV_RUST_EXTERNAL" in why
