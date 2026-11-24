"""A guard job past its deadline is RED and names itself (GOH.CHECKGIVEUP300).

Under the lane QoS clamp `goh land --check`'s guard tests hit a fixed 300 s and said "no verdict",
exit 0, and the lane enqueued past them: GOH.SUITEDRIFTBUILD's first land went red on 2 guards the
check had not judged. A check that judged nothing is never green: the job's deadline stretches by
the stretch measured on the check's own thread (the clamp the run queue cannot see), and a job past
that stretched deadline refuses, by name, in `goh land --check` and in the enqueue's own check.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import timing_bounds
from conftest import REPO_ROOT, git, write
from test_landq import _land_wt, _pending, env, landq  # noqa: F401 -- `env` is a fixture

sys.path.insert(0, str(REPO_ROOT))
from lib import land_check, proc_deadline

SLOW_GUARD = "# goh-land-guard:\nimport time\ndef test_slow_rule():\n    time.sleep({s})\n"


def _tree(tmp_path: Path, sleep_s: float) -> Path:
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_planted.py").write_text(SLOW_GUARD.format(s=sleep_s))
    return tree


@pytest.mark.load_sensitive  # a 30 s guard against a 1 s bound: red at any load
def test_a_planted_slow_guard_past_its_deadline_is_red_and_named(tmp_path, monkeypatch):
    tree = _tree(tmp_path, 30)
    monkeypatch.setattr(land_check, "STEP_TIMEOUT", 1)
    monkeypatch.setattr(proc_deadline, "stretch", lambda: 1.0)
    said: list[str] = []
    refused = land_check.run(tree, None, said.append, "")
    assert len(refused) == 1 and refused[0].startswith("guard tests 1/1"), (refused, said)
    assert "no verdict" in refused[0], refused
    assert any("NO VERDICT" in s and "red" in s for s in said), said


@pytest.mark.load_sensitive
def test_a_slow_guard_under_a_measured_clamp_is_waited_for(tmp_path, monkeypatch):
    """The deadline stretches by the clamp the check's thread measures: a guard that takes 3 s
    against a 2 s bound on a thread running x4 slower is waited for, and green."""
    tree = _tree(tmp_path, 3)
    monkeypatch.setattr(land_check, "STEP_TIMEOUT", 2)
    box = timing_bounds.stretch()  # and the box's own load on top: pytest's start is no guard
    monkeypatch.setattr(proc_deadline, "stretch", lambda: 4.0 * box)
    said: list[str] = []
    assert land_check.run(tree, None, said.append, "") == [], said


def test_the_stretched_deadline_is_capped():
    assert land_check.deadline(300, 2.0) == 600
    assert land_check.deadline(300, 0.5) == 300, "never below the quiet bound"
    assert land_check.deadline(300, 40.0) == land_check.STRETCH_CAP_S


def test_the_stretch_probe_reads_a_quiet_thread_as_one_and_a_slow_one_as_slow(monkeypatch):
    walls = iter([proc_deadline.REFERENCE_CPU_S * 3] * 5)
    monkeypatch.setattr(proc_deadline, "_timed_spin", lambda: next(walls))
    assert proc_deadline.stretch() == 3.0
    walls = iter([proc_deadline.REFERENCE_CPU_S / 2] * 5)
    assert proc_deadline.stretch() == 1.0, "never below a quiet core"


# THE ENQUEUE RUNS EVERY RULE (GOH.RULESLEFT). The enqueue's own check ran the declared guards and
# the learned refusers only: a rule test of the tree's shape (lib/rule_tests.py) that no file
# declared reached the lander, and the lane met it as a red land. With the clamp-stretched bound the
# rule set (230 tests, 30.5 s over 8 runs) runs at enqueue too, and a broken rule refuses there.
PLANTED_RULE = (
    "def test_no_planted_violation():\n    assert False, 'planted: a rule the tree breaks'\n"
)


def test_the_enqueue_check_runs_an_undeclared_rule_test_and_refuses_on_it(
    repo,
    tmp_path,
    env,  # noqa: F811
):
    wt, _ = _land_wt(repo, tmp_path)
    write(wt, "tests/test_rule.py", PLANTED_RULE)
    git(wt, "add", "tests/test_rule.py")
    git(wt, "commit", "-q", "--no-verify", "-m", "a planted rule")
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": "true", "GOH_LAND_CHECK_STEPS": "true"}
    done = landq(env, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.R", "--no-start")
    out = done.stdout + done.stderr
    assert done.returncode == 1 and "nothing queued" in out, out
    assert "tests/test_rule.py::test_no_planted_violation" in out, out
    assert _pending(tmp_path) == []


# THE BATCH'S RATCHET PHASE (lib/landq_ratchet.py) runs the same check on the stacked tree. A job
# with no verdict there used to refuse nothing; as a named refusal it must not evict an item: no
# node to re-run names a culprit, so the steps alone would find every lower item green and evict
# the top one for a job that judged nothing. A blind job leaves the stack to the gate.
def test_a_blind_job_in_the_ratchet_phase_evicts_no_item(tmp_path, monkeypatch):
    import landq_ratchet

    blind = "guard tests 2/8 (no verdict in 1500.0 s, exit 124)"
    assert land_check.blind(blind) and not land_check.blind("tests/test_x.py::test_no_y")
    monkeypatch.setattr(landq_ratchet.land_check, "run", lambda *a, **k: [blind])
    monkeypatch.setattr(landq_ratchet, "find", lambda *a: pytest.fail("a blind job named no one"))
    said: list[str] = []
    kit = SimpleNamespace(say=said.append)
    batch = SimpleNamespace(id="B", q=tmp_path, kit=kit, stack=lambda live: live)
    items = [SimpleNamespace(req={"worktree": str(tmp_path), "repo": "/r"}, tip=t) for t in "ab"]
    assert landq_ratchet.first(batch, items) == items
    assert any("no verdict" in s and "the gate decides" in s for s in said), said
