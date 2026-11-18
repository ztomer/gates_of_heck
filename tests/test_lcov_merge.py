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
  - no span forgiveness: a zero line inside an executed function is a miss,
    a zero-count clone's rows never lower a line another instantiation ran
"""

import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT

LCOV_MERGE = REPO_ROOT / "gates" / "lcov_merge.py"


def run_merge(parts_dir: Path, floor: str = "100"):
    return subprocess.run(
        [sys.executable, str(LCOV_MERGE), "--floor", floor, str(parts_dir)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def write_part(parts_dir: Path, name: str, text: str, bom: bool = False):
    parts_dir.mkdir(parents=True, exist_ok=True)
    data = ("\ufeff" if bom else "") + text
    (parts_dir / name).write_text(data, encoding="utf-8")


def part(fn_line=1, fn_name="_Za", fnda=5, das=((1, 1), (2, 1))):
    rows = ["SF:src/lib.rs", f"FN:{fn_line},{fn_name}", f"FNDA:{fnda},{fn_name}"]
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
        "DA:1,1",
        "DA:2,1",
        "DA:3,0",
        "DA:4,0",
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


def test_three_field_fn_lines_are_judged_by_their_own_counts(tmp_path):
    body = [
        "SF:src/lib.rs",
        "FN:1,4,_Za",
        "FNDA:5,_Za",
        "DA:1,1",
        "DA:2,0",
        "DA:3,1",
        "DA:4,1",
        "DA:8,0",  # beyond the declared end
        "end_of_record",
    ]
    pd = tmp_path / "parts"
    write_part(pd, "part-a.info", "\n".join(body) + "\n")
    r = run_merge(pd, floor="100")
    assert r.returncode == 1
    # line 2 is inside the executed span and was forgiven until GOH.SPANFORGIVE: both named.
    assert "src/lib.rs: 2, 8" in r.stdout, r.stdout


def test_malformed_da_exits_2_naming_file_and_line(tmp_path):
    pd = tmp_path / "parts"
    body = ["SF:src/lib.rs", "FN:1,_Za", "FNDA:1,_Za", "DA:1,oops", "end_of_record"]
    write_part(pd, "part-a.info", "\n".join(body) + "\n")
    r = run_merge(pd, floor="100")
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "part-a.info:4" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr


def test_malformed_fnda_exits_2_naming_file_and_line(tmp_path):
    pd = tmp_path / "parts"
    body = ["SF:src/lib.rs", "FN:1,_Za", "FNDA:notanum,_Za", "DA:1,1", "end_of_record"]
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


# ── per-target floors are refused, not ignored (roadmap 1.1, 2026-10-06) ─────
# `_load_floors` read target floors in both shapes and nothing applied them: a rust repo writing
# `{"mycrate": 100}` was gated on NOTHING (Rust rule #6, "no inert config that reads like a gate").
# The rust mode measures one workspace part, so a per-target floor is not measurable there.


def _floors_run(tmp_path: Path, floors: dict, *extra: str):
    import json

    parts = tmp_path / "parts"
    parts.mkdir()
    write_part(
        parts, "part-workspace.info", part(fnda=0, das=((1, 1), (2, 0)))
    )  # 50%, none forgiven
    (tmp_path / "floors.json").write_text(json.dumps(floors))
    return subprocess.run(
        [
            sys.executable,
            str(LCOV_MERGE),
            *extra,
            "--floors-json",
            str(tmp_path / "floors.json"),
            str(parts),
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def test_a_flat_per_target_floor_is_refused_not_ignored(tmp_path: Path) -> None:
    r = _floors_run(tmp_path, {"covfix": 100})
    assert r.returncode == 2, r.stdout + r.stderr
    assert "covfix" in r.stdout + r.stderr and "per-target" in r.stdout + r.stderr


def test_wrapped_target_floors_are_refused_not_ignored(tmp_path: Path) -> None:
    r = _floors_run(tmp_path, {"targets": {"covfix": 100}, "file_floor": 0}, "--floor", "0")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "covfix" in r.stdout + r.stderr


def test_a_file_floor_alone_is_still_honoured(tmp_path: Path) -> None:
    r = _floors_run(tmp_path, {"file_floor": 90})
    assert r.returncode == 1, r.stdout + r.stderr  # 50% < 90: a real refusal, not a config error


def test_an_unreadable_floors_file_is_refused_not_dropped(tmp_path: Path) -> None:
    """It was a warning and a pass on --floor alone: the per-file check silently fell away
    (ztools' notes, 2026-10-05; roadmap 1.4)."""
    parts = tmp_path / "parts"
    write_part(parts, "part-workspace.info", part())
    (tmp_path / "floors.json").write_text('{"file_floor": 90, // a comment json cannot read\n}')
    r = subprocess.run(
        [
            sys.executable,
            str(LCOV_MERGE),
            "--floor",
            "0",
            "--floors-json",
            str(tmp_path / "floors.json"),
            str(parts),
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 2, r.stdout + r.stderr


def test_an_exemption_may_name_its_file_relative_to_the_project(tmp_path: Path) -> None:
    """lcov's SF is absolute, so an exempt key was valid at ONE checkout path only -- not in a
    push gate's export, not on another machine (ztools, roadmap 1.3). A key relative to the
    project root (the merger's working directory) names the same file anywhere."""
    import json

    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    parts = proj / "parts"
    sf = proj / "src" / "driver.rs"
    sf.write_text("fn x() {}\n")
    write_part(
        parts,
        "part-workspace.info",
        f"SF:{sf}\nFN:1,_Zx\nFNDA:0,_Zx\nDA:1,0\nDA:2,0\nend_of_record\n",
    )
    (proj / "floors.json").write_text(
        json.dumps({"file_floor": 90, "exempt": {"src/driver.rs": "needs a real browser"}})
    )
    r = subprocess.run(
        [sys.executable, str(LCOV_MERGE), "--floors-json", "floors.json", "parts"],
        cwd=proj,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "stale" not in r.stdout + r.stderr, r.stdout + r.stderr


# ── no span forgiveness: a line is covered by its own count (GOH.SPANFORGIVE) ─
# The merger counted a zero-count line inside ANY executed function as covered: "span forgiveness",
# meant for phantom misses from zero-count clones of instantiated generics. A real producer has no
# such phantom: cargo-llvm-cov's DA rows are already the union over every instantiation of the file
# (fixtures/lcov_generics: cargo-llvm-cov 0.9.1, LLVM 23, `lib.rs` there -- `generic::<u8>` and the
# non-test build's `plain` are FNDA:0 clones, and every line some instantiation ran reads DA>0). So
# the span only ever forgave lines NO instantiation ran: an untaken `if` arm of a called function.

FIXTURES = Path(__file__).parent / "fixtures" / "lcov_generics"


def _missed(stdout: str) -> list[int]:
    rows = [r for r in stdout.splitlines() if r.startswith("  src/lib.rs: ")]
    return [int(n) for n in rows[0].split(": ", 1)[1].split(", ")] if rows else []


def test_a_never_run_line_inside_an_executed_function_reads_uncovered(tmp_path):
    """Two-field records (today's cargo-llvm-cov): _Za ran, its line 2 did not. It used to be
    forgiven by _Za's open-ended span and the floor read 100%."""
    pd = tmp_path / "parts"
    write_part(pd, "part-workspace.info", part(das=((1, 1), (2, 0), (3, 1))))
    r = run_merge(pd, floor="100")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "66.67%" in r.stdout and _missed(r.stdout) == [2], r.stdout


def test_a_zero_count_clone_of_an_instantiated_generic_reads_covered(tmp_path):
    """The case forgiveness was for, met by the union of DA rows: a clone's own zero rows, in the
    same part or another, never lower a line another instantiation ran."""
    pd = tmp_path / "parts"
    ran = "_RINvCsAAAA_2pa1gmEB2_"
    clone = "_RINvCsBBBB_2pa1ghEB2_"
    write_part(
        pd,
        "part-workspace.info",
        f"SF:src/lib.rs\nFN:1,{ran}\nFN:1,{clone}\nFNDA:4,{ran}\nFNDA:0,{clone}\n"
        "DA:1,4\nDA:2,4\nDA:1,0\nDA:2,0\nend_of_record\n",
    )
    write_part(
        pd,
        "part-external.info",
        f"SF:src/lib.rs\nFN:1,{clone}\nFNDA:0,{clone}\nDA:1,0\nDA:2,0\nend_of_record\n",
    )
    r = run_merge(pd, floor="100")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "100.0% of coverable lines" in r.stdout, r.stdout


def test_the_real_producers_generics_read_by_their_own_counts(tmp_path):
    """The real export: the untaken arms of three executed functions (lines 18, 26, 34 of
    fixtures/lcov_generics/lib.rs) are misses; the FNDA:0 clones of `generic`, `plain`, `inl` and
    `gen_branch` cost nothing; only never-run functions' lines join them. 29 of 38, never 32."""
    pd = tmp_path / "parts"
    pd.mkdir()
    (pd / "part-workspace.info").write_text(
        (FIXTURES / "part-workspace.info").read_text(encoding="utf-8"), encoding="utf-8"
    )
    r = run_merge(pd, floor="100")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "76.32% of coverable lines" in r.stdout and "29/38" in r.stdout, r.stdout
    assert _missed(r.stdout) == [11, 12, 13, 18, 26, 34, 39, 40, 41], r.stdout
