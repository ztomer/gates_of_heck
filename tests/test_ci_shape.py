"""A land's suite runs as hosted CI runs it (GOH.CIPARITY; tests/ci_shape.py, tests/suite_home.py).

Two drifts are red here. gate.yml gains a variable the leg does not carry: the workflow is read by
an independent scan (every `NAME:` under an `env:` block, every GITHUB_ENV write) and each name it
finds must be one the leg applied. And the leg itself drifts from its reading: each way gate.yml
can set a variable is planted in a copy, and the reading must carry it or refuse it by name.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import ci_shape
import pytest
import suite_home
from conftest import REPO_ROOT, hermetic_env

TREE_READS = (".github/workflows/gate.yml",)
GATE_YML = REPO_ROOT / ci_shape.WORKFLOW
# A variable gate.yml sets that never reaches the suite's step, each with why (GOH.CIWALL: the suite
# runs in the `suite` matrix job, not the gate's).
NOT_THE_GATES: dict[str, str] = {
    "GOH_LCI_TIMEOUT": "the gate job's local_ci step ceiling; the suite's job runs no local_ci",
    "GOH_COV_PART": "the partition: a land runs every part at once (tests/test_suite_shard.py)",
    "GOH_COV_PARTS_IN": "the merge job's input; that job runs no test",
}


def _scanned(text: str) -> set[str]:
    """Every name gate.yml sets, read without tests/ci_shape.py's parser: the instrument the leg
    is judged by must not share its reading."""
    names, env_at = set(), None
    for line in text.splitlines():
        body = line.lstrip(" ")
        if not body or body.startswith("#"):
            continue
        indent = len(line) - len(body)
        if env_at is not None and indent <= env_at:
            env_at = None
        if re.fullmatch(r"(- )?env:\s*", body):
            env_at = indent + (2 if body.startswith("- ") else 0)
            continue
        if env_at is not None and (m := re.match(r"([A-Za-z_][A-Za-z0-9_]*):", body)):
            names.add(m.group(1))
        names.update(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=[^\s]*\"?\s*>>\s*\"?\$GITHUB_ENV", line))
    return names


def _enter(tmp_path: Path, drop_shape: bool = True, **extra: str) -> dict:
    """What a FRESH suite entry (a land's, a ship's, a hand run's) leaves, under a real home of
    `tmp_path`'s holding a Projects with a live loop and a skills tree."""
    real = tmp_path / "real"
    (real / "Projects" / "repo").mkdir(parents=True)
    (real / "Projects" / ".loop").mkdir()
    (real / ".claude" / "skills").mkdir(parents=True)
    env = hermetic_env(**extra)
    env.pop(suite_home.MARKER, None)  # enter afresh
    if drop_shape:
        env.pop(suite_home.SHAPE, None)
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "CI", "GITHUB_ACTIONS", "CARGO_TERM_COLOR"):
        env.pop(name, None)
    env["HOME"] = str(real)
    env.update(extra)
    probe = (
        "import json, os, sys; sys.path.insert(0, sys.argv[1]); import suite_home; "
        "h = os.environ['HOME']; print(json.dumps({'env': dict(os.environ), "
        "'projects': os.path.exists(h + '/Projects/repo'), "
        "'skills': os.path.exists(h + '/.claude/skills')}))"
    )
    got = subprocess.run(
        [sys.executable, "-c", probe, str(REPO_ROOT / "tests")],
        env=env, cwd=tmp_path, capture_output=True, text=True, check=False,
        stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def test_every_variable_gate_yml_sets_is_one_the_leg_applies(tmp_path):
    """THE drift: a variable gate.yml's gate step gets and a land's suite does not is red."""
    scanned = _scanned(GATE_YML.read_text()) - set(NOT_THE_GATES)
    assert scanned, "the scan found no variable in gate.yml: it would prove nothing"
    applied = json.loads(
        _enter(tmp_path, GOH_LAND_VERDICTS_REUSE=str(tmp_path / "rec"))["env"][ci_shape.APPLIED]
    )
    assert scanned <= set(applied), (
        f"gate.yml sets {sorted(scanned - set(applied))}, the leg omits them"
    )
    assert {"CI", "GITHUB_ACTIONS"} <= set(applied)


@pytest.mark.parametrize("key", list(suite_home.LAND_KEYS))
def test_a_lands_suite_holds_none_of_the_owners_home_and_gate_ymls_variables(tmp_path, key):
    got = _enter(tmp_path, **{key: str(tmp_path / "record")})
    env, home = got["env"], got["env"]["HOME"]
    assert (got["projects"], got["skills"]) == (False, False)
    assert env[suite_home.SHAPE] == "ci"
    assert env["XDG_CONFIG_HOME"].startswith(home) and env["XDG_DATA_HOME"].startswith(home)
    want = ci_shape.overlay(GATE_YML, str(REPO_ROOT))
    # Every variable but the caller's GOH_*, which the entry drops on the runner too.
    assert {k: env.get(k) for k in want if not k.startswith("GOH_")} == {
        k: v for k, v in want.items() if not k.startswith("GOH_")
    }
    assert env["GOH_DIR"] == str(REPO_ROOT)


def test_a_hand_run_keeps_the_box_shape_and_can_ask_for_ci(tmp_path):
    box = _enter(tmp_path / "box")
    assert (box["projects"], box["skills"]) == (True, True)
    assert suite_home.SHAPE not in box["env"] and "GITHUB_ACTIONS" not in box["env"]
    ci = _enter(tmp_path / "ci", drop_shape=False, **{suite_home.SHAPE: "ci"})
    assert (ci["projects"], ci["skills"], ci["env"]["GITHUB_ACTIONS"]) == (False, False, "true")


def test_a_change_to_the_workflow_carries_no_verdict_across_it():
    """Every land test's environment is read from gate.yml: a change to it is suite-wide, so a land
    carries no test's verdict from the tree before it (tests/_land_verdicts.py `choose`)."""
    import _land_verdicts

    from lib import test_closure

    assert str(ci_shape.WORKFLOW) in test_closure.ENTRY_READS
    chosen, why = _land_verdicts.choose(REPO_ROOT, [str(ci_shape.WORKFLOW)])
    assert chosen is None and "gate.yml" in why


def test_a_shape_of_no_known_name_is_refused():
    with pytest.raises(SystemExit, match="none of box, ci"):
        suite_home.shape({suite_home.SHAPE: "hosted"})
    assert suite_home.shape({suite_home.SHAPE: "box", "GOH_LAND_VERDICTS_REUSE": "/r"}) == "ci"


BASE = """\
on: [push]
jobs:
  gate:
    runs-on: macos-26
    env:
      KEPT: "1"
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000 # v1
      - name: here
        run: echo "WHERE=$GITHUB_WORKSPACE" >> "$GITHUB_ENV"
      - name: The gate
        run: ./tools/gate.sh --full
"""


def _plant(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "gate.yml"
    path.write_text(text)
    return path


@pytest.mark.parametrize(
    ("text", "name", "value"),
    [
        (BASE, "KEPT", "1"),
        (BASE, "WHERE", "/ws"),
        ("env:\n  TOP: 'a'  # top\n" + BASE, "TOP", "a"),
        (
            BASE.replace("    env:\n", "    env:\n      NEWJOB: two words # c\n"),
            "NEWJOB",
            "two words",
        ),
        (
            BASE.replace(
                "run: ./tools/gate.sh --full",
                "run: ./tools/gate.sh --full\n        env:\n          STEP: s",
            ),
            "STEP",
            "s",
        ),
        (
            BASE.replace(
                "      - name: The gate",
                "      - run: |\n          set -e\n"
                '          echo "LATER=x" >> "$GITHUB_ENV"\n      - name: The gate',
            ),
            "LATER",
            "x",
        ),
    ],
    ids=["job", "github-env", "workflow", "job-added", "gate-step", "block-write"],
)
def test_each_way_gate_yml_sets_a_variable_is_carried(tmp_path, text, name, value):
    got = ci_shape.overlay(_plant(tmp_path, text), "/ws")
    assert got[name] == value
    assert got["GITHUB_ACTIONS"] == "true"
    assert name in _scanned(text)  # the independent scan sees it too


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (BASE.replace('KEPT: "1"', "KEPT: ${{ secrets.X }}"), "workflow expression"),
        (BASE.replace('KEPT: "1"', 'KEPT: "$RUNNER_TEMP/x"'), "shell variable"),
        (BASE.replace('KEPT: "1"', 'KEPT: "$GITHUB_WORKSPACE/x"'), "env: block never expands"),
        (
            BASE.replace(
                'echo "WHERE=$GITHUB_WORKSPACE" >> "$GITHUB_ENV"', "printf 'W=1' >> $GITHUB_ENV"
            ),
            "GITHUB_ENV write",
        ),
        (BASE.replace("./tools/gate.sh --full", "./tools/gate.sh"), "0 steps run"),
        (BASE + "      - run: ./tools/gate.sh --full\n", "2 steps run"),
    ],
    ids=["expression", "shell-var", "env-workspace", "write-form", "no-gate", "two-gates"],
)
def test_what_the_reading_cannot_evaluate_is_refused_by_name(tmp_path, text, why):
    with pytest.raises(ci_shape.ShapeError, match=why):
        ci_shape.overlay(_plant(tmp_path, text), "/ws")


def test_a_variable_set_in_another_step_does_not_reach_the_gate(tmp_path):
    text = BASE.replace("        run: echo", "        env:\n          OTHER: o\n        run: echo")
    assert "OTHER" not in ci_shape.overlay(_plant(tmp_path, text), "/ws")


SHARDED = (
    BASE.replace(
        "run: ./tools/gate.sh --full",
        "run: ./tools/gate.sh --full\n        env:\n"
        "          GOH_COV_PART: workspace\n          GATE_ONLY: g",
    )
    + """\
  suite:
    runs-on: macos-26
    strategy:
      fail-fast: false
      matrix:
        part: ["1/3", "2/3", '3/3']
    env:
      SUITE_JOB: j
    steps:
      - run: echo "WHERE=$GITHUB_WORKSPACE" >> "$GITHUB_ENV"
      - name: A shard
        run: ./gates/rust_gate.sh
        env:
          GOH_RUST_GROUPS: coverage
          GOH_COV_PART: ${{ matrix.part }}
"""
)


def test_a_sharded_workflow_gives_the_suite_its_shards_step(tmp_path):
    """GOH.CIWALL: the suite runs in the matrix step; its partition variable is no environment."""
    path = _plant(tmp_path, SHARDED)
    got = ci_shape.overlay(path, "/ws")
    assert (got["SUITE_JOB"], got["WHERE"], got["GOH_RUST_GROUPS"]) == ("j", "/ws", "coverage")
    assert "GOH_COV_PART" not in got and "GATE_ONLY" not in got and "KEPT" not in got
    assert ci_shape.partition(path) == ["1/3", "2/3", "3/3"]
    assert ci_shape.gate_part(path) == "workspace"
    assert ci_shape.partition(_plant(tmp_path, BASE)) == []


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (SHARDED.replace("        part: [", "        other: ["), "which it does not set"),
        (SHARDED.replace('["1/3", "2/3", \'3/3\']', "${{ fromJSON(x) }}"), "matrix axis"),
        (SHARDED + "      - run: x\n        env:\n          GOH_COV_PART: ${{ matrix.part }}\n",
         "2 steps shard the suite"),
    ],
    ids=["no-axis", "axis-expression", "two-sharded"],
)  # fmt: skip
def test_a_shard_shape_the_reading_cannot_evaluate_is_refused(tmp_path, text, why):
    with pytest.raises(ci_shape.ShapeError, match=why):
        ci_shape.partition(_plant(tmp_path, text))


def test_a_run_on_main_is_never_superseded():
    """GOH.CIWALL: every push to main is a release; cancel-in-progress cancelled 2 of 13. On main
    each run is its own concurrency group and nothing cancels it; a branch still cancels."""
    conc = ci_shape.parse(GATE_YML.read_text())["concurrency"]
    assert "github.ref == 'refs/heads/main' && github.sha" in conc["group"], conc
    assert conc["cancel-in-progress"] == "${{ github.ref != 'refs/heads/main' }}", conc
