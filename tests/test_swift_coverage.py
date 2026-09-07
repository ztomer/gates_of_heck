"""check_swift_coverage.py — parse/aggregate/refusal tests on checked-in
payload shapes. No Xcode required: SPM payloads are file-based, and the xcode
mode's tree-walk is tested at the parse level."""

import importlib.util
import json
import sys
from pathlib import Path

from conftest import REPO_ROOT, run_check

_spec = importlib.util.spec_from_file_location(
    "check_swift_coverage", REPO_ROOT / "checks" / "check_swift_coverage.py"
)
cov = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cov)

SCRIPT = "checks/check_swift_coverage.py"


def _write_spm(repo, rel, files):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "data": [
            {"files": [
                {"filename": n, "covered_lines": c, "total_lines": t}
                for n, c, t in files
            ]}
        ]
    }
    p.write_text(json.dumps(payload), encoding="utf-8")


def test_spm_aggregates_across_payloads(repo):
    _write_spm(repo, ".build/arm64-apple-macosx/debug/codecov/a.json",
               [("A.swift", 90, 100), ("B.swift", 10, 10)])
    _write_spm(repo, ".build/x86_64-unknown-linux/debug/codecov/b.json",
               [("C.swift", 5, 100)])
    r = run_check(repo, SCRIPT, "--min", "80")
    # (90+10+5)/(100+10+100) = 105/210 = 50% → fails with per-file list
    assert r.returncode == 1
    assert "50.0%" in r.stderr and "A.swift" in r.stderr


def test_spm_passes_above_floor(repo):
    _write_spm(repo, ".build/a/debug/codecov/a.json", [("A.swift", 96, 100)])
    r = run_check(repo, SCRIPT, "--min", "95")
    assert r.returncode == 0
    assert "96.0%" in r.stdout


def test_spm_missing_payload_is_named_refusal_not_zero(repo):
    # Honesty rule: no data must never masquerade as 0% (or as success).
    r = run_check(repo, SCRIPT, "--min", "50")
    assert r.returncode == 2
    assert "cannot measure" in r.stderr
    assert "enable-code-coverage" in r.stderr


def test_spm_corrupt_payload_is_named_refusal(repo):
    # Regression (2026-08-25): a payload present but unreadable used to fall
    # through the `why` check (paths non-empty) and aggregate to 100% —
    # corrupt coverage data masqueraded as a PASSING gate.
    p = repo / ".build" / "a" / "debug" / "codecov" / "bad.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json at all", encoding="utf-8")
    r = run_check(repo, SCRIPT, "--min", "50")
    assert r.returncode == 2
    assert "cannot measure" in r.stderr
    assert "none contained measurable files" in r.stderr


def test_spm_parseable_payload_with_no_measurable_files_refuses(repo):
    _write_spm(repo, ".build/a/debug/codecov/empty.json",
               [("Empty.swift", 0, 0)])
    r = run_check(repo, SCRIPT, "--min", "50")
    assert r.returncode == 2
    assert "1 payload(s) matched" in r.stderr


def test_spm_zero_line_entries_do_not_dilute(repo, monkeypatch):
    _write_spm(repo, ".build/a/debug/codecov/a.json",
               [("A.swift", 95, 100), ("Empty.swift", 0, 0)])
    monkeypatch.chdir(repo)
    records, paths = cov.load_spm(".build/a/debug/codecov/*.json")
    assert len(paths) == 1
    pct, files = cov.aggregate(records)
    assert len(files) == 1 and abs(pct - 95.0) < 1e-9


# ---- xcode tree-walk --------------------------------------------------------


XCRESULT_JSON = {
    "actions": [
        {"actionResult": {"coverage": {"codeCoverage": {
            "targets": [
                {"name": "CoreTests", "coveredLines": 190, "lineCount": 200},
                {"name": "App", "coveredLines": 3, "lineCount": 10},
            ]
        }}}}
    ]
}


def test_xcresult_walk_finds_target_records():
    recs = cov._xcresult_records(XCRESULT_JSON)
    assert ("CoreTests", 190, 200) in recs
    assert ("App", 3, 10) in recs


def test_xcresult_bools_are_not_ints():
    # bool is an int subclass in Python; a True lineCount must not count.
    recs = cov._xcresult_records({"coveredLines": True, "lineCount": True})
    assert recs == []


def test_aggregate_worst_first_ordering():
    pct, files = cov.aggregate([
        ("Good.swift", 99, 100), ("Bad.swift", 40, 100), ("Mid.swift", 80, 100)])
    assert [f[0] for f in files] == ["Bad.swift", "Mid.swift", "Good.swift"]
    assert abs(pct - (219 / 300 * 100)) < 1e-9


def test_xcode_mode_missing_dd_is_named_refusal(repo):
    r = run_check(repo, SCRIPT, "--min", "50", "--xcode")
    assert r.returncode == 2
    assert "no *.xcresult" in r.stderr


# --- llvm.coverage.json.export 3.x (Swift 6.3) --------------------------------
#
# The per-file counts moved under "summary"; a checker reading only the old
# total_lines/covered_lines saw zero measurable files and refused a perfectly
# healthy tree as "corrupt codecov JSON". Both shapes must work, because a
# repo's toolchain is not ours to pick.


def _write_spm_summary(repo, rel, files):
    """Write a payload in the modern export shape."""
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "3.0.1",
        "type": "llvm.coverage.json.export",
        "data": [
            {"files": [
                {
                    "filename": n,
                    "summary": {
                        "lines": {"count": t, "covered": c,
                                  "percent": (100.0 * c / t) if t else 0},
                        "functions": {"count": 1, "covered": 1, "percent": 100},
                    },
                }
                for n, c, t in files
            ]}
        ],
    }
    p.write_text(json.dumps(payload), encoding="utf-8")


def test_spm_reads_summary_shaped_payloads(repo):
    _write_spm_summary(repo, ".build/arm64-apple-macosx/debug/codecov/a.json",
                       [("/w/Sources/A.swift", 40, 100), ("/w/Sources/B.swift", 10, 100)])
    r = run_check(repo, SCRIPT, "--min", "20")
    # 50/200 = 25% >= 20
    assert r.returncode == 0, r.stderr
    assert "25.0%" in r.stdout + r.stderr


def test_summary_shape_still_fails_below_the_floor(repo):
    """The new reader must be able to REFUSE, not just parse."""
    _write_spm_summary(repo, ".build/arm64-apple-macosx/debug/codecov/a.json",
                       [("/w/Sources/A.swift", 1, 100)])
    r = run_check(repo, SCRIPT, "--min", "50")
    assert r.returncode == 1
    assert "A.swift" in r.stderr


def test_generated_sources_do_not_count(repo):
    """SwiftPM synthesises a test runner under .build. Counting it moves the
    number without moving the code under test, so it must be excluded."""
    _write_spm_summary(repo, ".build/arm64-apple-macosx/debug/codecov/a.json",
                       [("/w/Sources/A.swift", 10, 100),
                        ("/w/.build/arm64-apple-macosx/debug/Pkg.derived/runner.swift",
                         100, 100)])
    r = run_check(repo, SCRIPT, "--min", "50")
    # Counting the runner would read 110/200 = 55% and pass; excluding it
    # reads the real 10/100 = 10% and fails.
    assert r.returncode == 1, "generated runner was counted toward coverage"
    assert "10.0%" in r.stderr


def test_both_payload_shapes_agree(repo):
    """Same numbers, two shapes, one answer."""
    _write_spm(repo, ".build/a/debug/codecov/old.json", [("/w/A.swift", 30, 100)])
    old = run_check(repo, SCRIPT, "--min", "0")
    (repo / ".build/a/debug/codecov/old.json").unlink()
    _write_spm_summary(repo, ".build/a/debug/codecov/new.json", [("/w/A.swift", 30, 100)])
    new = run_check(repo, SCRIPT, "--min", "0")
    assert "30.0%" in old.stdout + old.stderr
    assert "30.0%" in new.stdout + new.stderr


def test_test_sources_do_not_count(repo):
    """A test file is ~100% covered by definition; counting it lets a floor be
    met by adding tests that assert nothing."""
    _write_spm_summary(repo, ".build/arm64-apple-macosx/debug/codecov/a.json",
                       [("/w/Sources/A.swift", 10, 100),
                        ("/w/Tests/ATests.swift", 100, 100)])
    r = run_check(repo, SCRIPT, "--min", "50")
    # Counting the test file reads 110/200 = 55% and passes; excluding it
    # reads the real 10/100 = 10% and fails.
    assert r.returncode == 1, "a test source was counted toward coverage"
    assert "10.0%" in r.stderr
