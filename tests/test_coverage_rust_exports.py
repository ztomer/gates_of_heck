"""coverage_gate.sh rust mode — export completeness and the lcov merger's FN
parsing, driven END TO END through the real bash gate against a STUBBED cargo
(the same fake-toolchain pattern as the swift tests): the lcov payloads are
hand-written, no Rust toolchain required.

Contracts pinned here:
  - the ONE run of every declared target must exit 0 (its .ok marker); a failed
    run hard-fails naming every target it lost, a file left behind or not
  - a valid-but-empty run is named, and the merger refuses a report over zero lines
  - FN records parse in BOTH live formats: two-field (start,name — today's
    cargo-llvm-cov) and three-field (start,end,name — geninfo)
  - a three-field fn's forgiveness span is bounded by its declared end;
    two-field spans stay open-ended exactly as before

Red proofs: the shortfall and empty-part tests were written FIRST and failed
against pre-fix coverage_gate.sh (failed exports were warned away and the run
continued; empty parts passed silently). The three-field forgiveness test
failed against pre-fix code (three-field names parsed as "end,mame", so FNDA
never matched and nothing was forgiven).
"""

import json
import os
import subprocess
from pathlib import Path

from conftest import REPO_ROOT

COV_GATE = REPO_ROOT / "gates" / "coverage_gate.sh"

CARGO_STUB = """#!/bin/bash
# Stubbed cargo: canned `metadata` JSON + canned per-target lcov exports.
if [ "$1" = "metadata" ]; then cat "${CARGO_STUB_META:?}"; exit 0; fi
# Every build-driving call records where cargo was told to build.
echo "target=${CARGO_TARGET_DIR:-} build=${CARGO_BUILD_BUILD_DIR:-}" \
  >> "${CARGO_STUB_ENVLOG:-/dev/null}"
if [ "$1 $2" = "llvm-cov clean" ]; then exit 0; fi
tname=""; out=""; prev=""
for a in "$@"; do
  case "$prev" in
    --test) tname="$a" ;;
    --output-path) out="$a" ;;
  esac
  prev="$a"
done
[ -n "$out" ] || exit 2
parts="${CARGO_STUB_PARTS:?}"
# ONE run of every target (`--workspace --lib --tests`): its export is every canned part, in order.
src="$(mktemp)"; cat "$parts"/*.info > "$src" 2>/dev/null
if [ -n "${CARGO_STUB_DROP:-}" ]; then
  exit 1   # the run fails WITHOUT producing an output file
fi
if [ -n "${CARGO_STUB_FAIL_LEAVE:-}" ]; then
  # The LAUNDERING shape: the run FAILS but leaves its output file behind.
  if [ -n "${CARGO_STUB_LEAVE_TEXT:-}" ]; then printf '%s' "$CARGO_STUB_LEAVE_TEXT" > "$out"; else cp "$src" "$out"; fi
  exit 1
fi
cp "$src" "$out"
exit 0
"""

META = {
    "packages": [
        {
            "name": "covfix",
            "version": "0.1.0",
            "targets": [
                {"name": "covfix", "kind": ["lib"]},
                {"name": "all", "kind": ["test"]},
            ],
        }
    ]
}


def build_part(
    fn_start: int, fn_name: str, fnda: int, das: list[tuple[int, int]], fn_end: int | None = None
) -> str:
    lines = ["SF:src/lib.rs"]
    if fn_end is None:
        lines.append(f"FN:{fn_start},{fn_name}")
    else:
        lines.append(f"FN:{fn_start},{fn_end},{fn_name}")
    lines.append(f"FNDA:{fnda},{fn_name}")
    for ln, cnt in das:
        lines.append(f"DA:{ln},{cnt}")
    lines.append("end_of_record")
    return "\n".join(lines) + "\n"


def setup_fixture(tmp_path: Path, parts: dict[str, str], meta=META) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cargo = bin_dir / "cargo"
    cargo.write_text(CARGO_STUB)
    cargo.chmod(0o755)
    meta_file = tmp_path / "meta.json"
    meta_file.write_text(json.dumps(meta))
    parts_dir = tmp_path / "canned-parts"
    parts_dir.mkdir()
    for name, text in parts.items():
        (parts_dir / name).write_text(text)
    return proj


def run_rust_gate(
    proj: Path,
    bin_dir: Path,
    meta_file: Path,
    parts_dir: Path,
    floor: str = "100",
    drop: str | None = None,
    fail_leave: str | None = None,
    extra_env: dict[str, str] | None = None,
):
    env = dict(os.environ)
    env.update(extra_env or {})
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.pop("GOH_COV_FLOOR_RUST", None)
    env["CARGO_STUB_META"] = str(meta_file)
    env["CARGO_STUB_PARTS"] = str(parts_dir)
    if drop:
        env["CARGO_STUB_DROP"] = drop
    else:
        env.pop("CARGO_STUB_DROP", None)
    if fail_leave is not None:
        env["CARGO_STUB_FAIL_LEAVE"] = "1"
        env["CARGO_STUB_LEAVE_TEXT"] = fail_leave
    else:
        env.pop("CARGO_STUB_FAIL_LEAVE", None)
        env.pop("CARGO_STUB_LEAVE_TEXT", None)
    return subprocess.run(
        ["/bin/bash", str(COV_GATE), "--lang", "rust", "--floor", floor, str(proj)],
        cwd=proj,
        capture_output=True,
        text=True,
        env=env,
    )


def missed_lines(output: str) -> list[int]:
    """Parse the exact uncovered-line list out of the merger's report."""
    in_section = False
    for line in output.splitlines():
        if "uncovered lines" in line:
            in_section = True
            continue
        if in_section and ": " in line:
            return [int(x) for x in line.split(": ")[1].split(",")]
    return []


FULL_COVER = [(n, 1) for n in range(1, 5)] + [(n, 1) for n in range(6, 13)]


# ── B: export completeness ──────────────────────────────────────────────────


def test_a_failed_run_hard_fails_naming_every_target_it_lost(tmp_path):
    """One run measures every target, so a run that fails loses all of them -- and names them."""
    parts = {"lib.info": build_part(1, "_Za", 5, FULL_COVER)}
    proj = setup_fixture(tmp_path, parts)
    r = run_rust_gate(
        proj, tmp_path / "bin", tmp_path / "meta.json", tmp_path / "canned-parts", drop="all"
    )
    assert r.returncode == 1, r.stdout + r.stderr
    combined = r.stdout + r.stderr
    assert "expected but missing" in combined
    assert "covfix (test all)" in combined and "covfix (lib)" in combined, combined


def test_a_valid_but_empty_run_is_named_and_measures_nothing(tmp_path):
    """The run succeeded and found nothing coverable: named, and never read as a pass -- the
    merger refuses a report over zero lines."""
    parts = {"lib.info": "", "all.info": ""}
    proj = setup_fixture(tmp_path, parts)
    r = run_rust_gate(proj, tmp_path / "bin", tmp_path / "meta.json", tmp_path / "canned-parts")
    combined = r.stdout + r.stderr
    assert "EMPTY" in combined, combined
    assert r.returncode != 0 and "no coverable lines" in combined, combined


# ── B2: the LAUNDERING hole — a failing export that leaves its file behind ──
# Completeness keyed on part-file existence/size used to pass these: the file
# was there, so "100%" was reported over garbage. Red-proofed against pre-fix
# coverage_gate.sh (both variants exited 0).


def test_failed_export_leaving_EMPTY_part_hard_fails(tmp_path):
    parts = {"lib.info": "", "all.info": build_part(1, "_Za", 5, FULL_COVER)}
    proj = setup_fixture(tmp_path, parts)
    r = run_rust_gate(
        proj, tmp_path / "bin", tmp_path / "meta.json", tmp_path / "canned-parts", fail_leave=""
    )
    assert r.returncode == 1, r.stdout + r.stderr
    combined = r.stdout + r.stderr
    assert "expected but missing" in combined
    assert "covfix (lib)" in combined, "the failed export must be NAMED"


def test_failed_export_leaving_GARBAGE_part_hard_fails(tmp_path):
    # A partial/garbage export left behind by a FAILED run: without the
    # exit-0 marker this must never count as measured coverage.
    parts = {"lib.info": build_part(1, "_Za", 5, FULL_COVER)}
    proj = setup_fixture(tmp_path, parts)
    r = run_rust_gate(
        proj,
        tmp_path / "bin",
        tmp_path / "meta.json",
        tmp_path / "canned-parts",
        fail_leave="SF:src/lib.rs\nDA:1,1\nDA:2,1\ngarbage-truncated-rec",
    )
    assert r.returncode == 1, r.stdout + r.stderr
    combined = r.stdout + r.stderr
    assert "expected but missing" in combined
    assert "covfix (lib)" in combined


def test_every_declared_target_exported_is_green(tmp_path):
    parts = {
        "lib.info": build_part(1, "_Za", 5, FULL_COVER),
        "all.info": build_part(1, "_Za", 5, FULL_COVER),
    }
    proj = setup_fixture(tmp_path, parts)
    r = run_rust_gate(proj, tmp_path / "bin", tmp_path / "meta.json", tmp_path / "canned-parts")
    assert r.returncode == 0, r.stdout + r.stderr


# ── C: merger FN parsing ────────────────────────────────────────────────────
# Fixture shape: fn _Za declared lines 1-4 (three-field), executed (FNDA 5);
# line 2 is a phantom zero-count row INSIDE the span; lines 8-10 are
# module-tail rows BEYOND the declared end.


THREE_FIELD_LIB = build_part(
    1,
    "_Za",
    5,
    [(1, 1), (2, 0), (3, 1), (4, 1), (8, 0), (9, 0), (10, 0)],
    fn_end=4,
)
THREE_FIELD = {"lib.info": THREE_FIELD_LIB, "all.info": THREE_FIELD_LIB}


def test_uncovered_line_inside_three_field_span_is_forgiven(tmp_path):
    proj = setup_fixture(tmp_path, THREE_FIELD)
    r = run_rust_gate(proj, tmp_path / "bin", tmp_path / "meta.json", tmp_path / "canned-parts")
    assert r.returncode == 1, r.stdout + r.stderr
    missed = missed_lines(r.stdout)
    assert missed == [8, 9, 10], (
        f"line 2 (inside the declared span) must be forgiven; got missed={missed}"
    )


def test_module_tail_beyond_three_field_end_is_NOT_forgiven(tmp_path):
    proj = setup_fixture(tmp_path, THREE_FIELD)
    r = run_rust_gate(proj, tmp_path / "bin", tmp_path / "meta.json", tmp_path / "canned-parts")
    assert r.returncode == 1, r.stdout + r.stderr
    missed = missed_lines(r.stdout)
    assert set(missed) == {8, 9, 10}, "tail lines past the declared end must stay uncovered"
    # The denominator stays honest: 4 of 7 coverable lines ran.
    pct_line = next(l for l in r.stdout.splitlines() if "[coverage]" in l)
    assert "57.14%" in pct_line, pct_line


TWO_FIELD_LIB = build_part(
    1,
    "_Za",
    5,
    [(1, 1), (2, 0), (3, 1), (4, 1), (8, 0), (9, 0)],
    fn_end=None,
)
TWO_FIELD = {"lib.info": TWO_FIELD_LIB, "all.info": TWO_FIELD_LIB}


def test_two_field_corpus_keeps_open_ended_spans_exactly_as_before(tmp_path):
    """Legacy byte-identical behavior: without a declared end, the executed
    fn's open-ended span forgives EVERYTHING after it (lines 2, 8, 9 here).
    Changing this moves live cargo-llvm-cov consumers' numbers — pinned."""
    proj = setup_fixture(tmp_path, TWO_FIELD)
    r = run_rust_gate(proj, tmp_path / "bin", tmp_path / "meta.json", tmp_path / "canned-parts")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "100%" in r.stdout


# ── B: cpp mode — a failing suite must stop the measurement ─────────────────


def test_cpp_ctest_failure_is_a_hard_fail_naming_itself(tmp_path):
    proj = setup_fixture(tmp_path, {})  # dir + cargo stub only; cpp ignores them
    bin_dir = tmp_path / "cppbin"
    bin_dir.mkdir()
    for name, body in {
        "cmake": "#!/bin/bash\nexit 0\n",
        "ctest": "#!/bin/bash\nexit 1\n",
        "xcrun": "#!/bin/bash\nexit 0\n",
    }.items():
        f = bin_dir / name
        f.write_text(body)
        f.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.pop("GOH_COV_FLOOR_CPP", None)
    r = subprocess.run(
        ["/bin/bash", str(COV_GATE), "--lang", "cpp", "--floor", "80", str(proj)],
        cwd=proj,
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    combined = r.stdout + r.stderr
    # The NEW contract wording: a hard refusal, not a "may be partial" warn.
    assert "ctest reported failures" in combined
    assert "refusing to measure partial coverage" in combined


# ── C: build isolation ──────────────────────────────────────────────────────
def test_coverage_build_never_shares_the_build_dir(tmp_path):
    """The instrumented build must be fully isolated. `CARGO_TARGET_DIR`
    alone stopped isolating it the day cargo grew `build.build-dir`: with a
    machine-wide build-dir (the house layout since 2026-09-20), every
    crate's intermediate artifacts -- the test binaries cargo-llvm-cov
    exports from -- live in ONE directory shared with ordinary builds. The
    gate then merged stale instrumented binaries from the previous source
    into the report: lines past the end of the current file, all "uncovered",
    and a 96% tree read 93.5% (routines, 2026-09-21). Every build-driving
    cargo call must see the build-dir pinned to the gate's own target dir."""
    proj = setup_fixture(
        tmp_path,
        {
            "lib.info": build_part(1, "f", 1, FULL_COVER),
            "all.info": build_part(1, "f", 1, FULL_COVER),
        },
    )
    envlog = tmp_path / "envlog"
    r = run_rust_gate(
        proj,
        tmp_path / "bin",
        tmp_path / "meta.json",
        tmp_path / "canned-parts",
        extra_env={"CARGO_STUB_ENVLOG": str(envlog), "CARGO_BUILD_BUILD_DIR": "/shared/build"},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    calls = envlog.read_text().splitlines()
    assert calls, "the stub saw no cargo calls"
    isolated = str(proj / "target" / "llvm-cov")
    for call in calls:
        assert call == f"target={isolated} build={isolated}", call
