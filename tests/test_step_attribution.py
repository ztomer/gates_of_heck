"""lib/step_attribution.py: a timed step whose rows leave its seconds unnamed is a finding.

GOH.RELEASELANDGATE. v0.139.4's release timings named its land as 160 s, and the 94 rows under it
added up to 6 s: a pre-step pytest run wrote no row. Calibrated both ways: that shape is found, and
the same land with its pre-step and gate as rows of their own is not; a leaf is attributed, a gap
under MIN_GAP_S is no finding, and children run side by side never sum past their parent.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import step_attribution

CLI = REPO_ROOT / "lib" / "step_attribution.py"
LAND = "release: land (its release gate)"


def row(label: str, s: float, parent: str = "") -> dict:
    return {"label": label, "ms": s * 1000, "rc": 0, "tier": "phase", "parent": parent}


def v0_139_4() -> list[dict]:
    """The release step as v0.139.4 recorded it: the land's 94 rows are its two structural runs."""
    checks = [row(f"check {i}", 0.067, LAND) for i in range(94)]
    return [row("ship release", 650.4), row(LAND, 159.7, "ship release"),
            row("release: push main", 484.6, "ship release"), *checks]  # fmt: skip


def test_the_v0_139_4_land_is_found_with_its_unnamed_seconds() -> None:
    (gap,) = step_attribution.gaps(v0_139_4())
    assert gap["label"] == LAND and gap["children"] == 94, gap
    assert 153 < (gap["ms"] - gap["named_ms"]) / 1000 < 154, gap
    assert "unattributed" in step_attribution.describe(gap)


def test_the_same_land_with_its_parts_as_rows_has_no_gap() -> None:
    rows = [row("ship release", 40.0), row(LAND, 30.0, "ship release"),
            row("release: push main", 9.0, "ship release"),
            row("land: gate", 28.0, LAND), row("release gate: structural", 6.0, "land: gate"),
            row("release gate: 1 test files", 21.0, "land: gate")]  # fmt: skip
    assert step_attribution.gaps(rows) == []


def test_a_leaf_is_attributed_and_a_small_gap_is_no_finding() -> None:
    assert step_attribution.gaps([row("one long leaf", 600.0)]) == []
    small = [row("phase", 8.0), row("part", 4.0, "phase")]  # 4 s unnamed, under MIN_GAP_S
    assert step_attribution.gaps(small) == []
    big = [row("phase", 80.0), row("part", 40.0, "phase")]
    assert [g["label"] for g in step_attribution.gaps(big)] == ["phase"]


def test_side_by_side_children_never_sum_past_their_parent() -> None:
    rows = [row("fan-out", 10.0), row("a", 9.0, "fan-out"), row("b", 9.0, "fan-out")]
    assert step_attribution.gaps(rows) == []
    (g,) = step_attribution.gaps([row("p", 100.0), row("a", 30.0, "p"), row("b", 30.0, "p")])
    assert g["named_ms"] == 60_000, g


def test_the_check_exits_1_on_a_gap_and_0_without(tmp_path: Path) -> None:
    def check(rows: list[dict]) -> subprocess.CompletedProcess[str]:
        f = tmp_path / "t.jsonl"
        f.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return subprocess.run([sys.executable, str(CLI), "check", str(f)], env=hermetic_env(),
                              capture_output=True, text=True, timeout=patience(60),
                              stdin=subprocess.DEVNULL, check=False)  # fmt: skip

    red = check(v0_139_4())
    assert red.returncode == 1 and LAND in red.stdout, red.stdout + red.stderr
    green = check([row("ship release", 10.0), row("x", 9.5, "ship release")])
    assert green.returncode == 0 and not green.stdout, green.stdout + green.stderr
