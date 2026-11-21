"""The batch's gate reads the enqueue's pre-step verdict where its tree is the enqueued one
(GOH.BATCHPREFROM).

lib/landq_batch.py's gate started tools/land.sh without GOH_LAND_PRE_FROM, so the lander never saw
the verdict `goh land --enqueue` kept: a lone item gated on main exactly as enqueued ran the whole
pre-step again, which SUITEONCE's reuse exists to spare (found by GOH.PRESTEPDUP). The verdict is
passed only when the gated tree IS the enqueued commit's tree: a stack of two moved the tip, and
that tree is judged again.

The fixture: `one` plants the gate's red file, so the stack of two is red and bisects into two
lone items, each gated on main at its enqueued tree. GOH_LAND_PRE logs the sha of every run.
"""

from __future__ import annotations

from pathlib import Path

from conftest import git
from test_landq_batch import env, landq, lane, recorded  # noqa: F401  (env: a fixture)

from lib import log_follow


def _pre_runs(log: Path) -> list[str]:
    return log.read_text().split() if log.exists() else []


def test_a_lone_item_at_its_enqueued_tree_reuses_the_enqueue_verdict_a_stack_does_not(
    repo: Path,
    tmp_path: Path,
    env: dict[str, str],  # noqa: F811
) -> None:
    pre_log = tmp_path / "pre.log"
    env = {**env, "GOH_LAND_PRE": f"git rev-parse HEAD >> {pre_log}", "GOH_GATE_SLOTS": "1"}
    wts = [
        (lane(repo, tmp_path, "one", {"one.txt": "1\n", "bad.txt": "planted\n"}), "T.ONE"),
        (lane(repo, tmp_path, "two", {"two.txt": "2\n"}), "T.TWO"),
    ]
    for wt, item in wts:
        done = landq(
            env, "--enqueue", "--foreground", "--land", str(wt), "--item", item, "--no-start"
        )
        assert done.returncode == 0, done.stdout + done.stderr
    enqueued = [git(wt, "rev-parse", "HEAD").strip() for wt, _ in wts]
    assert _pre_runs(pre_log) == enqueued  # the enqueue's own pre-step, once per item
    drained = landq(env, "--drain")
    assert drained.returncode == 0, drained.stdout + drained.stderr
    got = recorded(env)
    assert got["T.ONE"]["state"] == "red" and got["T.TWO"]["state"] == "landed", got
    runs = _pre_runs(pre_log)[2:]
    # The stack's tip (two on one) is a tree no enqueue judged: its pre-step runs. Each lone item
    # after the bisect is gated at its enqueued tree: the enqueue's verdict holds, nothing runs.
    assert len(runs) == 1 and runs[0] not in enqueued, (runs, enqueued)
    logs = sorted((Path(env["GOH_LANDQ_DIR"]) / "logs").glob("*.gate*.log"))
    held = [p.name for p in logs if "the verdict the enqueue judged" in p.read_text()]
    assert len(logs) == 3 and len(held) == 2, [p.read_text() for p in logs]
    assert not list((Path(env["GOH_LANDQ_DIR"]) / "pre").glob("*")), (
        "a kept verdict outlived its batch"
    )


# THE REASON A GATE MEASURES. PRESTEPDUP predicted 3 of 24 lands plain that measured, and nothing
# said why: the coverage step's decision now reaches the land's log (lib/log_follow.py, the gate's
# follower) and the lander names it in lander.log per land.
PLAIN = "↔ [rust] coverage: measuring -- the last measurement was on other inputs"
GREY, RESET = chr(27) + "[90m", chr(27) + "[0m"  # tui `measure`'s colour around its glyph
DECISION = PLAIN.replace("↔", f"{GREY}↔{RESET}", 1)


def test_the_follower_carries_the_coverage_decision_without_its_colour(capsys) -> None:
    tail = log_follow._show(f"noise\n  {DECISION}\n↔ coverage 93.4% (floor 93.1%)\n", "")
    assert tail == ""
    assert capsys.readouterr().out.splitlines() == [PLAIN]


def test_the_lander_says_why_a_land_s_gate_measured(
    repo: Path,
    tmp_path: Path,
    env: dict[str, str],  # noqa: F811
) -> None:
    env = {**env, "GOH_LANDQ_BATCH": "1", "GOH_LAND_GATE": f"printf '%s\\n' '{DECISION}'"}
    wt = lane(repo, tmp_path, "one", {"one.txt": "1\n"})
    done = landq(
        env,
        "--enqueue",
        "--foreground",
        "--land",
        str(wt),
        "--item",
        "T.ONE",
        "--no-start",
        "--no-pre",
        "fixture",
    )
    assert done.returncode == 0, done.stdout + done.stderr
    drained = landq(env, "--drain")
    out = drained.stdout + drained.stderr
    assert drained.returncode == 0, out
    assert (
        "T.ONE: the gate's coverage: measuring -- the last measurement was on other inputs" in out
    ), out
