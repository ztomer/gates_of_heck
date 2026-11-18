"""Which code judges a goh lane's land (GOH.PRESTEPHEAD, docs/decisions/D-0012).

The queue ran the WORKTREE's own tools/land.sh (GOH.PRESTEPSHARE's enqueue printed a deadline its
change had not landed yet), and land.sh gated with the tip's own gates: a lane that weakened
land.sh, or a gate, was judged by its own weakened copy and passed itself. Now the queue always
drives with main's land.sh (tools/landq.py land_script), and a goh land whose diff changes the
gate code is gated twice: by main tip's gates on the lane's tree (the judge a lane cannot edit),
then by the tip's own (a gate change still proves itself before main moves).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import commit_all, git, hermetic_env, native_goh_path, write
from test_gate_environment import _clean_checkout_of_todays_gates
from test_landq import FAKE_LOOPCTL, _land_wt, _pending, landq, recorded
from timing_bounds import patience

# A lane's weakened land.sh: no pre-step, no gate -- it fast-forwards main itself and says so.
WEAK_LAND = """\
#!/usr/bin/env bash
[ -n "${GOH_LAND_PRE_ONLY:-}" ] && exit 0
main="$(git worktree list --porcelain | { read -r _ p; printf %s "$p"; })"
git -C "$main" merge --ff-only -q "$(git rev-parse HEAD)" && echo "landed by the lane's own land.sh"
"""


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    return hermetic_env(drop_git=True, GOH_LANDQ_DIR=str(tmp_path / "landq"),
                        GOH_LOOPCTL=str(fake), FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
                        GOH_LAND_TIMEOUT="60")  # fmt: skip


def _weak_lane(repo: Path, tmp_path: Path) -> tuple[Path, str]:
    wt, _ = _land_wt(repo, tmp_path)
    write(wt, "tools/land.sh", WEAK_LAND)
    commit_all(wt, "the lane weakens its land.sh")
    return wt, git(wt, "rev-parse", "HEAD").strip()


def test_the_enqueue_pre_step_is_mains_not_a_lanes_weakened_land_sh(repo, tmp_path, env):
    wt, _ = _weak_lane(repo, tmp_path)
    pre = "echo 'FAILED tests/test_wiring.py::test_planted - planted'; exit 1"
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": pre}
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.H", "--no-start")
    out = done.stdout + done.stderr
    assert done.returncode != 0 and "tests/test_wiring.py::test_planted" in out, out
    assert _pending(tmp_path) == []


def test_the_lander_gates_with_mains_land_sh_not_a_lanes_weakened_one(repo, tmp_path, env):
    wt, _ = _weak_lane(repo, tmp_path)
    before = git(repo, "rev-parse", "HEAD").strip()
    env = {**env, "GOH_LAND_GATE": "echo main-gate-ran; false"}
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.K", "--no-start",
                 "--no-pre", "the test gates in the lander")  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    landq(env, "--drain")
    got = recorded(env)["T.K"]
    assert got["state"] == "red" and "main-gate-ran" in Path(got["log"]).read_text(), got
    assert git(repo, "rev-parse", "HEAD").strip() == before


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       capture_output=True, text=True, env=hermetic_env(drop_git=True),
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout.strip()


PROBE = (
    '[ ! -e bad.txt ] || {{ echo "probe {who}: bad.txt is in the tree"; exit 1; }}\n'
    'echo "probe {who} ran"\n'
)


def test_a_lane_that_weakens_a_gate_is_judged_by_mains_gate_too(tmp_path: Path) -> None:
    """Both directions on one checkout of today's gates: a lane that weakens the gate it would
    fail is red under main's copy; a lane that edits the gate harmlessly runs BOTH and lands."""
    main = _clean_checkout_of_todays_gates(tmp_path)
    (main / "gates" / "zz_land_probe.sh").write_text(PROBE.format(who="main"))
    _git(main, "add", "gates/zz_land_probe.sh")
    _git(main, "commit", "-q", "--no-verify", "-m", "main's strict probe gate")
    env = hermetic_env(drop_git=True, GOH_LAND_GATE='bash "$GOH_DIR/gates/zz_land_probe.sh"',
                       GOH_LAND_PRE="")  # fmt: skip
    for k in ("GOH_LIVE", "GOH_LIVE_ROOT", "PYTEST_CURRENT_TEST", "PYTHONPATH"):
        env.pop(k, None)
    env.update(GOH_DIR=str(main), GOH_HEAD_CACHE=str(tmp_path / "head-cache"),
               GOH_BIN=str(native_goh_path()),
               GATES_OF_HECK_SUITE_RUNNER=str(os.getpid()))  # fmt: skip

    def lane(name: str, probe: str, bad: bool) -> subprocess.CompletedProcess:
        wt = tmp_path / name
        _git(main, "worktree", "add", "-q", "-b", name, str(wt))
        (wt / "gates" / "zz_land_probe.sh").write_text(probe)
        if bad:
            (wt / "bad.txt").write_text("what main's gate refuses\n")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-q", "--no-verify", "-m", name)
        return subprocess.run(["bash", str(wt / "tools" / "land.sh")], cwd=wt, env=env, text=True,
                              capture_output=True, timeout=patience(300),
                              stdin=subprocess.DEVNULL, check=False)  # fmt: skip

    before = _git(main, "rev-parse", "HEAD")
    weak = lane("weakens-the-gate", "exit 0\n", bad=True)
    out = weak.stdout + weak.stderr
    assert weak.returncode != 0 and "probe main: bad.txt is in the tree" in out, out
    assert _git(main, "rev-parse", "HEAD") == before
    good = lane("edits-the-gate", PROBE.format(who="lane"), bad=False)
    out = good.stdout + good.stderr
    assert good.returncode == 0, out
    assert "probe main ran" in out and "probe lane ran" in out, out
    assert out.index("probe main ran") < out.index("probe lane ran"), out
