"""Per-target Swift coverage floors in checks/check_swift_coverage.py.

Why they exist: a package's overall percentage is dominated by whichever
target has the most lines. On a SwiftUI app that is the views, which no unit
test executes, so the package number can sit comfortably above its floor
while the target that holds all the logic rots. A floor that cannot notice
that is a floor on the wrong thing.

Why this lives here rather than being delegated to coverage_gate.sh: that
path re-runs `swift test` and measures a different denominator (see
test_coverage_swift_scope.py). Teaching the existing checker per-target
floors keeps one fast measurement and one definition of scope.
"""

import importlib.util
import json
import sys

import pytest

from conftest import REPO_ROOT


def _mod():
    path = REPO_ROOT / "checks" / "check_swift_coverage.py"
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    spec = importlib.util.spec_from_file_location("check_swift_cov_ut", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_swift_cov_ut"] = mod
    spec.loader.exec_module(mod)
    return mod


# (name, covered, total) — the shape load_spm/load_xcode produce.
RECORDS = [
    ("/p/Sources/Logic/Parse.swift", 90, 100),
    ("/p/Sources/Logic/Model.swift", 60, 100),
    ("/p/Sources/Views/Big.swift", 5, 800),
]


def test_a_target_is_the_first_path_segment_after_sources():
    m = _mod()
    assert m.spm_target_of("/p/Sources/Logic/Parse.swift") == "Logic"
    assert m.spm_target_of("/p/Sources/Views/Sub/Deep.swift") == "Views"
    # No Sources/ in the path: fall back to the leading segment rather than
    # inventing a target name.
    assert m.spm_target_of("Other/Thing.swift") == "Other"


def test_per_target_totals_split_the_package():
    m = _mod()
    per = m.per_target(RECORDS)
    assert per["Logic"] == (150, 200)
    assert per["Views"] == (5, 800)


def test_a_healthy_logic_target_passes_while_the_package_is_low():
    """The whole point: 15.5% overall, but Logic is 75%."""
    m = _mod()
    code, lines = m.check_target_floors(RECORDS, {"Logic": 70.0}, 0.0)
    assert code == 0, lines
    assert any("75.00%" in ln for ln in lines)


def test_a_rotting_logic_target_fails_even_though_the_package_floor_is_met():
    m = _mod()
    code, lines = m.check_target_floors(RECORDS, {"Logic": 80.0}, 0.0)
    assert code == 1
    assert any("under its 80% floor" in ln for ln in lines)


def test_a_target_matching_no_source_is_an_error_not_a_pass():
    """The hole this closes.

    The sibling implementation scored an unmatched target 100% and let it
    through, so renaming a target -- or misspelling one -- turned its floor
    into one that could never fail. A floors file is the thing a reader
    trusts to say what is enforced, so an entry that enforces nothing has to
    be loud.
    """
    m = _mod()
    code, lines = m.check_target_floors(RECORDS, {"Logik": 70.0}, 0.0)
    assert code == 2
    assert any("matched no measured source" in ln for ln in lines)
    # And it names what IS there, so the fix is obvious.
    assert any("Logic" in ln and "Views" in ln for ln in lines)


def test_an_empty_measurement_cannot_satisfy_any_floor():
    m = _mod()
    code, _ = m.check_target_floors([], {"Logic": 1.0}, 0.0)
    assert code == 2, "no records must not pass a floor by vacuity"


def test_both_floors_file_shapes_load(tmp_path):
    m = _mod()
    wrapper = tmp_path / "w.json"
    wrapper.write_text(json.dumps({"targets": {"Logic": 70}, "tolerance": 0.5}))
    assert m.load_floors(str(wrapper)) == ({"Logic": 70.0}, 0.5)

    flat = tmp_path / "f.json"
    flat.write_text(json.dumps({"Logic": 70}))
    assert m.load_floors(str(flat)) == ({"Logic": 70.0}, 0.0)


def test_a_floors_file_that_enforces_nothing_is_refused(tmp_path):
    m = _mod()
    for body in ("{}", '{"targets": {}}', "[]"):
        bad = tmp_path / "bad.json"
        bad.write_text(body)
        with pytest.raises(SystemExit) as exc:
            m.load_floors(str(bad))
        assert exc.value.code == 2, body
    missing = tmp_path / "nope.json"
    with pytest.raises(SystemExit) as exc:
        m.load_floors(str(missing))
    assert exc.value.code == 2
