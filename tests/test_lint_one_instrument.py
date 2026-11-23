"""The ruff a lane runs and the ruff the gate runs are ONE instrument here (GOH.LINTDISAGREE).

Three lanes on 2026-11-21 reported E501/E402/RUF021 "already on base" in files they touched
(hooks/claude/agent_guard.py, lib/busyloop.py, lib/name_kill.py, two tests) while the commit gate
passed them. The two did not disagree about the rules: this repo's pyproject selects the house set
(GOH_RUFF_CONFIG_PARITY=on refuses it if it drops one), so a plain `ruff check` and the gate's ruff
read the same config and enable the same rules. The gate held those findings as ceilinged debt
(tools/py_lint_ceilings.json) and said nothing; plain ruff printed them. The findings are fixed and
their ceilings gone; at commit, debt that remains is labelled `baseline` (GOH.LINTASGATE).

These hold the one-instrument property on goh's own tree: same config file, same enabled rules,
same findings, for every file the lanes named.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "gates"))
import py_lint_ceiling

pytestmark = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")

NAMED = [
    "hooks/claude/agent_guard.py",
    "hooks/claude/discard_guard.py",
    "lib/busyloop.py",
    "lib/name_kill.py",
    "tests/test_claude_agent_guard.py",
    "tests/test_model_choice_rules.py",
]
RUNNER = f"{sys.executable} -m"


def _gatesrc(key: str) -> str | None:
    for line in (REPO_ROOT / ".gatesrc").read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def test_the_gate_refuses_a_config_that_drops_a_house_rule():
    assert _gatesrc(py_lint_ceiling.PARITY) == "on"
    assert _gatesrc(py_lint_ceiling.KEY) == "tools/py_lint_ceilings.json"


@pytest.mark.parametrize("rel", NAMED)
def test_plain_ruff_reads_the_config_and_rules_the_gate_reads(rel, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    missing, where = py_lint_ceiling.unselected(RUNNER, rel)
    assert where == "pyproject.toml", where
    assert missing == [], f"plain ruff misses {missing} the gate judges in {rel}"


def test_plain_ruff_finds_what_the_gate_finds_and_no_ceiling_hides_one(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    gate = py_lint_ceiling.findings(RUNNER, NAMED)
    plain = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--output-format", "json", "--exit-zero", *NAMED],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    plain_codes = sorted((f["filename"], f["code"]) for f in json.loads(plain.stdout))
    gate_codes = sorted((f["filename"], f["code"]) for v in gate.values() for f in v)
    assert plain_codes == gate_codes
    ceilings = json.loads((REPO_ROOT / "tools" / "py_lint_ceilings.json").read_text())
    held = {rel: ceilings[rel] for rel in NAMED if rel in ceilings}
    assert held == {}, f"debt the lanes kept re-reporting is held again: {held}"
    assert gate_codes == [], gate_codes
