"""What the enqueue's pre-step did with the lane's run of the set, on the land's record
(GOH.LANEPRESTEP).

A lane runs `tools/affected_tests.py --run` on its committed tip and the enqueue's pre-step takes
that kept verdict when the tree, set and suite inputs are the same (lib/affected_verdict.py). The
request and its done record say which happened -- `pre_affected`: a take (the sha judged, by whom,
the seconds not repeated, how many tests) or a run (why) -- so the item's metric, affected-test runs
per tree before the land gate, is counted from the records (24 lands measured from transcripts).
"""

from __future__ import annotations

import json
import shlex

import pytest
from test_landq import _land_wt, _pending, env, landq  # noqa: F401  -- `env` is the queue's fixture

TAKE_SAID = (
    "✓ pre-land: the set's verdict on 77089a59 holds (same tree, set and suite inputs; "
    "tools/affected_tests.py --run, 429.7 s, 1477 tests) -- not run again"
)
RAN_SAID = (
    "→ pre-land: no verdict taken (the nearest kept verdict (on 8d7a606b, 1 min old) "
    "differs in tree, selection, identity) -- it runs"
)


@pytest.mark.parametrize(
    ("said", "want"),
    [
        (TAKE_SAID, {"took": "77089a59", "by": "tools/affected_tests.py --run", "lane_s": 429.7,
                     "tests": 1477}),
        (RAN_SAID, {"ran": "the nearest kept verdict (on 8d7a606b, 1 min old) differs in tree, "
                           "selection, identity"}),
    ],
)  # fmt: skip
def test_the_request_and_its_done_record_say_what_the_pre_step_did_with_the_lanes_run(
    repo,
    tmp_path,
    env,  # noqa: F811  -- the queue's fixture
    said,
    want,
):
    """GOH.LANEPRESTEP: whether the enqueue took the lane's kept verdict (and the seconds of the
    run it did not repeat) or ran the set, and why -- on the request and on its done record."""
    wt, _ = _land_wt(repo, tmp_path)
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": f"echo {shlex.quote(said)}"}
    done = landq(env, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.A", "--no-start")
    assert done.returncode == 0, done.stdout + done.stderr
    (path,) = _pending(tmp_path)
    assert json.loads(path.read_text()).get("pre_affected") == want
    drained = landq(env, "--drain")
    assert drained.returncode == 0, drained.stdout + drained.stderr
    (rec,) = (tmp_path / "landq" / "done").glob("*-T.A.json")
    assert json.loads(rec.read_text()).get("pre_affected") == want
