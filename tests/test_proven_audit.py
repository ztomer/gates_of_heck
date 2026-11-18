"""The proven-step cache under the reuse audit (GOH.REUSEAUDIT2, gates/_proven_audit.sh): a hit a
step runner is about to skip is counted, one in the `step` path's rate is run anyway and compared,
and a repeated disagreement turns the path off, so every later hit runs.

The calibration is a PLANTED under-keyed closure (GOH.PROVENCLOSURE): lib/step_closure.py replaced
by one that keys every step on the same line, so a goh file the step reads changes under its key
and the proof still hits. The shadow must catch it, and the step must run from then on.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from env_probe import print_safe
from test_proven import (  # noqa: F401  (fixtures)
    _git,
    env,
    goh,
    hooked,
    local_ci,
    proven,
    repo,
    runs,
)

sys.path.insert(0, str(REPO_ROOT / "lib"))
import reuse  # lib/, after its path
import reuse_audit

FLAG_STEP = 'echo ran >> "$COUNT"; test "$(cat "$GOH_DIR/gates/flag.txt")" = ok'
PLANT = (
    "import sys\n"
    "print('closure planted\\tfull')\n"
    "for i, a in enumerate(sys.argv):\n"
    "    if sys.argv[i - 1] == '--step':\n"
    "        print('closure planted\\t' + a)\n"
)


def _flag(gates: Path, value: str) -> None:
    (gates / "gates" / "flag.txt").write_text(value + "\n")
    _git(gates, "add", "-A")
    _git(gates, "commit", "-q", "-m", f"flag {value}")


@pytest.fixture
def planted(goh: Path, env: dict, tmp_path: Path) -> dict:  # noqa: F811
    """The closure keying on, its reader planted, the flag ok, a loopctl that logs what it files."""
    (goh / "lib" / "step_closure.py").write_text(PLANT)
    _flag(goh, "ok")
    log = tmp_path / "loopctl.jsonl"
    fake = tmp_path / "loopctl.py"
    fake.write_text(f"import json, sys\nopen({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]))\n")
    return print_safe(dict(env, GOH_PROVEN_CLOSURE="on", GOH_LOOPCTL=str(fake), FILED=str(log)))


def _advance(n: int, backstop: bool = False) -> None:
    """n hits counted, so the next hit's place in the rate is the test's to name."""
    fields = dict.fromkeys(reuse.PATHS["step"].fields, "advance")
    assert reuse_audit.use_many("step", [fields] * n, backstop) == ["reuse"] * n


def test_a_planted_under_keyed_closure_is_caught_and_turns_step_off(goh, repo, planted):  # noqa: F811
    assert os.environ["GOH_REUSE_DIR"] == planted["GOH_REUSE_DIR"]  # one store, both sides
    assert reuse.PATHS["step"].every_unbacked == 4  # the repo tracks no workflow: unbacked
    first = proven(goh, repo, planted, FLAG_STEP)
    assert first.returncode == 0 and runs(planted) == 1, first.stdout + first.stderr
    _advance(3)
    agreed = proven(goh, repo, planted, FLAG_STEP)  # the 4th hit: sampled, run, agrees
    assert agreed.returncode == 0 and runs(planted) == 2, agreed.stdout + agreed.stderr
    assert "the reuse audit sampled this hit" in agreed.stdout
    assert reuse_audit.state("step")["window"]["agreed"] == 1
    _flag(goh, "bad")  # the step now fails; the planted closure keeps its key
    lie = proven(goh, repo, planted, FLAG_STEP)
    assert lie.returncode == 0 and runs(planted) == 2, "the plant did not under-key the step"
    _advance(2)
    caught = proven(goh, repo, planted, FLAG_STEP)  # the 8th hit: sampled, red, and red again
    assert caught.returncode == 1 and runs(planted) == 4, caught.stdout + caught.stderr
    assert "the proven record said green" in caught.stdout + caught.stderr
    rec = reuse_audit.state("step")
    assert rec["state"] == "off" and rec["cause"] == "disagree", rec
    assert rec["evidence"]["reused"] == "green" and rec["evidence"]["fresh"] == "red"
    assert rec["evidence"]["again"] == {"reused": "green", "fresh": "red"}
    assert rec["evidence"]["fields"]["closure"] == "on"
    assert rec["fault"] == "filed GOH.REUSEOFFSTEP"
    [call] = [json.loads(x) for x in Path(planted["FILED"]).read_text().splitlines()]
    assert json.loads(call[call.index("--json") + 1])["id"] == "GOH.REUSEOFFSTEP"
    off = proven(goh, repo, planted, FLAG_STEP)  # off: every hit runs
    assert off.returncode == 1 and runs(planted) == 5, off.stdout + off.stderr
    assert "turned the step path off" in off.stdout


def test_local_ci_shadows_a_hit_and_a_repeated_red_turns_step_off(goh, hooked, planted):  # noqa: F811
    (hooked / ".gatesrc").write_text(f"GOH_CI_STEPS='{FLAG_STEP}:true'\n")
    _git(hooked, "add", "-A")
    _git(hooked, "commit", "-q", "-m", "flag step", "--no-verify")
    first = local_ci(goh, hooked, planted)
    assert first.returncode == 0 and runs(planted) == 1, first.stdout + first.stderr
    _flag(goh, "bad")
    _advance(3)
    caught = local_ci(goh, hooked, planted)  # the flag step's hit is the 4th: sampled
    assert caught.returncode != 0 and runs(planted) == 3, caught.stdout + caught.stderr
    assert "the reuse audit sampled this hit" in caught.stdout
    rec = reuse_audit.state("step")
    assert rec["state"] == "off" and rec["evidence"]["again"] == {"reused": "green", "fresh": "red"}
    assert rec["window"]["uses"] == 5  # `true`'s hit was asked too, after it: reused


def test_a_repo_hosted_ci_gates_is_sampled_at_the_path_s_slower_rate():
    p = reuse.PATHS["step"]
    assert p.shadow == "inline" and p.every > p.every_unbacked
    fields = dict.fromkeys(p.fields, "v")
    backed = reuse_audit.use_many("step", [fields] * p.every)
    assert [n for n, d in enumerate(backed) if d == "shadow"] == [p.every - 1]
    unbacked = reuse_audit.use_many("step", [fields] * 8, backstop=False)
    assert [n for n, d in enumerate(unbacked) if d == "shadow"] == [3, 7]
    win = reuse_audit.state("step")["window"]
    assert win["uses"] == p.every + 8 and win["unbacked"] == 8 and win["sampled"] == 3
