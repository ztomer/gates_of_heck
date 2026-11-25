"""Which code judges a goh lane's land (GOH.PRESTEPHEAD, docs/decisions/D-0012).

The queue ran the WORKTREE's own tools/land.sh (GOH.PRESTEPSHARE's enqueue printed a deadline its
change had not landed yet), so a lane that weakened land.sh passed itself: now the queue always
drives with main's land.sh (tools/landq.py land_script). The gate's STEPS are the lane's own files
under any gates, so a land runs one gate, the tip's, and main's version of each step the lane
changed is run after it and reported beside the tip's, never a refusal (GOH.ONEGATE).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _todays_gates import land_gates
from conftest import commit_all, git, hermetic_env, native_goh_path, write
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
    done = landq(env, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.H", "--no-start")
    out = done.stdout + done.stderr
    assert done.returncode != 0 and "tests/test_wiring.py::test_planted" in out, out
    assert _pending(tmp_path) == []


def test_the_lander_gates_with_mains_land_sh_not_a_lanes_weakened_one(repo, tmp_path, env):
    wt, _ = _weak_lane(repo, tmp_path)
    before = git(repo, "rev-parse", "HEAD").strip()
    env = {**env, "GOH_LAND_GATE": "echo main-gate-ran; false"}
    done = landq(env, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.K", "--no-start",
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


STEP = "./gates/zz_land_probe.sh"
SAME = "./gates/zz_land_same.sh"
PROBE = (
    "#!/usr/bin/env bash\n"
    'echo "{who} $GOH_DIR" >>"$ZZ_RAN"\n'
    '[ ! -e bad.txt ] || {{ echo "probe {who}: bad.txt is in the tree"; exit 1; }}\n'
)


def test_one_gate_under_the_tips_code_and_mains_code_reports_after_it(tmp_path: Path) -> None:
    """A lane that weakens the gate it would fail LANDS: the gate runs once, and a step is the tip's
    file whichever gates run it (D-0012 said main's copy refused it; its old proof ran main's file
    by path, as no step of goh's list does). Main's version of it is run after the land, red beside
    the tip's green, and reported; a step the lane did not change runs in the land alone."""
    main = land_gates(tmp_path, "lib/land_judge_owe.py", "lib/land_judge.py")
    for name, who in ((STEP, "main"), (SAME, "same")):
        (main / name).write_text(PROBE.format(who=who) if name == STEP else
                                 f'echo "{who} $GOH_DIR" >>"$ZZ_RAN"\n')  # fmt: skip
        (main / name).chmod(0o755)
    (main / ".gatesrc").write_text(f"GOH_CI_STEPS='{STEP}:bash {SAME}'\n")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "--no-verify", "-m", "main's strict probe gate")
    ran, db = tmp_path / "ran", tmp_path / "goh.db"
    env = hermetic_env(drop_git=True, GOH_LAND_GATE=f"{STEP} && bash {SAME}", GOH_LAND_PRE="",
                       ZZ_RAN=str(ran), GOH_DB=str(db), GOH_LANDQ_DIR=str(tmp_path / "landq"),
                       GOH_LOAD_DIR=str(tmp_path / "load"))  # fmt: skip
    for k in ("GOH_LIVE", "GOH_LIVE_ROOT", "PYTEST_CURRENT_TEST", "PYTHONPATH"):
        env.pop(k, None)
    # A box with room, which the drain asks for before each step (lib/land_judge_room.py).
    env.update(GOH_CPU_IDLE_READING="90", GOH_LANE_MIN_FREE_GIB="0", GOH_LANE_MAX_WAITING="99",
               GOH_GATE_SLOT_DIR=str(tmp_path / "slots"))  # fmt: skip
    env.update(GOH_DIR=str(main), GOH_HEAD_CACHE=str(tmp_path / "head-cache"),
               GOH_BIN=str(native_goh_path()),
               GATES_OF_HECK_SUITE_RUNNER=str(os.getpid()))  # fmt: skip
    wt = tmp_path / "lane"
    _git(main, "worktree", "add", "-q", "-b", "weakens-the-gate", str(wt))
    (wt / STEP).write_text('#!/usr/bin/env bash\necho "lane $GOH_DIR" >>"$ZZ_RAN"\n')
    (wt / "bad.txt").write_text("what main's gate refuses\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "--no-verify", "-m", "weakens the gate")
    tip = _git(wt, "rev-parse", "HEAD")

    def run(*argv: str) -> str:
        done = subprocess.run(argv, cwd=wt, env=env, text=True, capture_output=True, check=False,
                              timeout=patience(300), stdin=subprocess.DEVNULL)  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout + done.stderr

    # Main's driver, as the queue runs it: one gate, the lane's own files, under the tip's gates.
    out = run("bash", str(main / "tools" / "land.sh"))
    assert _git(main, "rev-parse", "HEAD") == tip and out.count("→ gate: ") == 1, out
    assert [(w, Path(d).name) for w, d in map(str.split, ran.read_text().splitlines())] == [
        ("lane", tip), ("same", tip)]  # fmt: skip
    judge = [sys.executable, str(main / "lib" / "land_judge.py")]
    got = json.loads(run(*judge, "report", "--json").splitlines()[0])
    assert (got["lands"], got["waiting"], got["named"]) == (1, 1, []), out
    run(*judge, "drain", "--main", str(main))
    got = json.loads(run(*judge, "report", "--json").splitlines()[0])
    assert (got["done"], got["judged"], got["no_row"]) == (1, 1, 0), got
    assert got["steps"] == {"would_red": 1, "same": 1}, got
    (named,) = got["named"]
    assert (named["tip"], named["step"], named["main_rc"], named["tip_rc"]) == (tip, STEP, 1, 0)
    assert "bad.txt" in named["reason"], named
    assert [ln.split()[0] for ln in ran.read_text().splitlines()][2:] == ["main", "main", "lane"]
