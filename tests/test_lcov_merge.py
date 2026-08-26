"""gates/lcov_merge.py — direct unit tests against the extracted merger.

The module was extracted verbatim from coverage_gate.sh's heredoc; the
gate-level ported tests (test_coverage_rust_exports.py) pin byte-identical
behavior for well-formed input end-to-end. These test the hardenings and the
module surface directly:

  - BOM'd part: utf-8-sig open keeps the first SF record's lines in the
    denominator (a BOM used to silently drop them — false 100% vs true 75%)
  - FN format detection by int-parsability: a two-field record whose mangled
    name contains a comma used to crash on int('_Z4other')
  - malformed DA/FNDA records exit 2 NAMING file+line (never an uncaught
    traceback masquerading as exit-1 below-floor)
"""

import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT

LCOV_MERGE = REPO_ROOT / "gates" / "lcov_merge.py"


def run_merge(parts_dir: Path, floor: str = "100"):
    return subprocess.run(
        [sys.executable, str(LCOV_MERGE), "--floor", floor, str(parts_dir)],
        capture_output=True, text=True,
    )


def write_part(parts_dir: Path, name: str, text: str, bom: bool = False):
    parts_dir.mkdir(parents=True, exist_ok=True)
    data = ("\ufeff" if bom else "") + text
    (parts_dir / name).write_text(data, encoding="utf-8")


def part(fn_line=1, fn_name="_Za", fnda=5, das=((1, 1), (2, 1))):
    rows = [f"SF:src/lib.rs", f"FN:{fn_line},{fn_name}",
            f"FNDA:{fnda},{fn_name}"]
    rows += [f"DA:{ln},{cnt}" for ln, cnt in das]
    rows.append("end_of_record")
    return "\n".join(rows) + "\n"


def test_bom_part_keeps_first_record_in_the_denominator(tmp_path):
    """A BOM before 'SF:' must not drop that record set. Fixture: fn spans
    lines 1-2 only (three-field, executed); lines 3-4 stay uncovered —
    truth is 2 of 4 = 50%, never a laundered 100%."""
    pd = tmp_path / "parts"
    body = [
        "SF:src/lib.rs",
        "FN:1,2,_Za",
        "FNDA:5,_Za",
        "DA:1,1", "DA:2,1", "DA:3,0", "DA:4,0",
        "end_of_record",
    ]
    write_part(pd, "part-a.info", "\n".join(body), bom=True)
    r = run_merge(pd, floor="100")
    assert r.returncode == 1, f"BOM laundered misses into green: {r.stdout!r}"
    assert "50.0%" in r.stdout, r.stdout


def test_two_field_fn_with_comma_in_mangled_name_parses(tmp_path):
    """'FN:10,_Z4other,x' — name contains a comma; field-count-based format
    detection crashed with ValueError: int('_Z4other')."""
    pd = tmp_path / "parts"
    body = [
        "SF:src/lib.rs",
        "FN:10,_Z4other,x",
        "FNDA:3,_Z4other,x",
        "DA:10,1",
        "DA:11,1",
        "end_of_record",
    ]
    write_part(pd, "part-a.info", "\n".join(body) + "\n")
    r = run_merge(pd, floor="100")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "100%" in r.stdout


def test_three_field_fn_still_bounded_by_declared_end(tmp_path):
    body = [
        "SF:src/lib.rs",
        "FN:1,4,_Za",
        "FNDA:5,_Za",
        "DA:1,1", "DA:2,0", "DA:3,1", "DA:4,1",
        "DA:8,0",  # beyond declared end: NOT forgiven
        "end_of_record",
    ]
    pd = tmp_path / "parts"
    write_part(pd, "part-a.info", "\n".join(body) + "\n")
    r = run_merge(pd, floor="100")
    assert r.returncode == 1
    # line 2 forgiven (inside executed span); line 8 named.
    assert "src/lib.rs: 8" in r.stdout, r.stdout


def test_malformed_da_exits_2_naming_file_and_line(tmp_path):
    pd = tmp_path / "parts"
    body = ["SF:src/lib.rs", "FN:1,_Za", "FNDA:1,_Za", "DA:1,oops",
            "end_of_record"]
    write_part(pd, "part-a.info", "\n".join(body) + "\n")
    r = run_merge(pd, floor="100")
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "part-a.info:4" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr


def test_malformed_fnda_exits_2_naming_file_and_line(tmp_path):
    pd = tmp_path / "parts"
    body = ["SF:src/lib.rs", "FN:1,_Za", "FNDA:notanum,_Za", "DA:1,1",
            "end_of_record"]
    write_part(pd, "part-a.info", "\n".join(body) + "\n")
    r = run_merge(pd, floor="100")
    assert r.returncode == 2
    assert "part-a.info:3" in r.stderr


def test_no_parts_at_all_reports_nothing_coverable(tmp_path):
    pd = tmp_path / "empty-parts"
    pd.mkdir()
    r = run_merge(pd, floor="90")
    assert r.returncode == 1
    assert "no coverable lines" in r.stdout
