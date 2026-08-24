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
