"""A gate's TESTS never write into the gate's own profile (GOH.TIMINGSBLEED).

v0.139.4's release profile held 1475 timing rows and 1068 of them were the suite's fixtures: the
coverage step ran the Rust tests with the release's `GOH_TIMINGS` in their environment, and every
fixture gate a test started appended its steps to the ship's file, under the coverage step's
label. The coverage gate keeps its own phase rows and hands the tests it runs no profile at all
(lib/timings_hold.sh); the plain `cargo test` of a measured run likewise (gates/rust_gate.sh).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import REPO_ROOT
from test_coverage_phases import _gate, _setup
from timing_bounds import patience

# What a fixture gate does inside a test: append its step to whatever profile it inherited.
LEAK = ('[ -z "${GOH_TIMINGS:-}" ] || printf \'{"label":"fixture %s","ms":1,"rc":0}\\n\' '
        '"${GOH_TIMINGS_PARENT:-}" >>"$GOH_TIMINGS"; exit 0')  # fmt: skip


def test_the_coverage_gate_s_tests_write_no_row_to_the_parent_profile(tmp_path: Path) -> None:
    proj, env = _setup(tmp_path)
    timings = tmp_path / "t.jsonl"
    env |= {"GOH_TIMINGS": str(timings), "GOH_TIMINGS_PARENT": "coverage (floor 0%)",
            "GOH_TIMINGS_KIND": "rust", "FAKE_TEST_BODY": LEAK}  # fmt: skip
    r = subprocess.run(_gate(proj, LEAK), cwd=proj, capture_output=True, text=True, env=env,
                       timeout=patience(120), stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    rows = [json.loads(x) for x in timings.read_text().splitlines() if x.strip()]
    assert not [x for x in rows if x["label"].startswith("fixture")], rows
    # the gate's own rows still land, named under the step and of the step's kind
    phases = [x for x in rows if x["label"].startswith("coverage phase: ")]
    assert phases and {x["parent"] for x in phases} == {"coverage (floor 0%)"}, rows
    assert {x.get("kind") for x in phases} == {"rust"}, rows


def test_the_plain_cargo_test_runs_with_no_profile() -> None:
    """A measured repo's plain run (rust_coverage_plain) runs both its test commands unbound."""
    text = (REPO_ROOT / "gates" / "_rust_coverage.sh").read_text()
    body = text.split("rust_coverage_plain() {", 1)[1].split("\n}\n", 1)[0]
    assert "goh_timings_unbind" in body, body
    assert body.count('env "${unbind[@]}"') == 2, body
