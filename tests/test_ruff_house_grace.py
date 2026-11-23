"""A repo's own ruff config without the house set is red from the grace's end (GOH.RUFFHOUSESET).

Three CIP lanes on 2026-11-15 found their repo's ruff config without the house rule set: a plain
`ruff check` there missed 73 or 323 rules the gate judges, and goh only warned. The gate itself
cannot fall behind -- every goh ruff step passes the set as `--extend-select`
(gates/ruff_house.py) -- so the drift is the repo's copy for hand runs. A vended base config was
probed and is not by construction: a repo's own `select` replaces a base's `extend-select`, and an
`extend = "${GOH_DIR}/..."` path fails ruff outright wherever GOH_DIR is unset. So the copy is
judged: from `ruff_house.OVER_DAY` an unset or `warn` GOH_RUFF_CONFIG_PARITY is `on`, and a repo
with no ceilings whose files the house set would flag is refused, not warned. Until then both warn
and name the day.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT, commit_all, stage, write
from test_py_lint_ceiling import LONG_LINE, MUTABLE_DEFAULT, ONE_RULE

GATE = REPO_ROOT / "gates" / "py_staged.sh"
CEILINGS = "GOH_PY_LINT_CEILINGS=tools/py_lint_ceilings.json\n"

pytestmark = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")


def run(repo, grace: str):
    return subprocess.run(
        ["/bin/bash", str(GATE), str(repo)], capture_output=True, text=True, check=False,
        stdin=subprocess.DEVNULL, env={**os.environ, "GOH_GRACE": grace},
    )  # fmt: skip


def _debt(repo, gatesrc: str):
    write(repo, ".gatesrc", gatesrc)
    write(repo, "tools/py_lint_ceilings.json", json.dumps({"old.py": 1}) + "\n")
    write(repo, "old.py", LONG_LINE)
    commit_all(repo, "debt")
    write(repo, "old.py", LONG_LINE + "Z = 1\n")
    stage(repo, "old.py")


@pytest.mark.parametrize("gatesrc", [CEILINGS, CEILINGS + "GOH_RUFF_CONFIG_PARITY=warn\n"])
def test_a_config_without_the_house_set_warns_within_the_grace_and_is_red_after(repo, gatesrc):
    _debt(repo, gatesrc)
    r = run(repo, "within")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "no ruff config here selects the house set" in out and "red from 2026-12-07" in out, out
    r = run(repo, "over")
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "no ruff config here selects the house set" in out, out


def test_unadopted_house_set_warns_within_the_grace_and_is_red_after(repo):
    write(repo, "ruff.toml", ONE_RULE)
    write(repo, "new.py", MUTABLE_DEFAULT)
    stage(repo, "ruff.toml", "new.py")
    r = run(repo, "within")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "not adopted: 1 finding here" in out, out
    assert "red from 2026-12-07" in out, out
    r = run(repo, "over")
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "not adopted: 1 finding here" in out, out


def test_the_named_day_is_the_day_that_decides():
    import sys

    sys.path.insert(0, str(REPO_ROOT / "gates"))
    try:
        import ruff_house
    finally:
        sys.path.remove(str(REPO_ROOT / "gates"))
    from datetime import UTC, datetime

    day = datetime.fromtimestamp(ruff_house.OVER_FROM, tz=UTC)
    assert day.strftime("%Y-%m-%d") == ruff_house.OVER_DAY == "2026-12-07"
    assert (day.hour, day.minute, day.second) == (0, 0, 0)
