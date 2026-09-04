"""Parity: legacy SPM helper vs strictest engine agree on shared fixtures.

checks/check_swift_coverage.py (swift_gate's helper: codecov-JSON
aggregation) and gates/coverage_swift.py::process (coverage_gate's llvm-cov
engine) measure DIFFERENTLY but must never DISAGREE on the same code: a repo
passing one gate and failing the other on identical coverage is a gate that
contradicts itself.

This test runs BOTH against one 50%-covered fixture (conftest's fake swift
toolchain: 10 lines, half covered) and pins identical verdicts above and
below the truth. It deliberately does NOT merge them — different methods,
different callers, and no real swift toolchain here to oracle-verify a merge
against. It closes the silent-drift class the duplication creates: if either
implementation's arithmetic moves, this goes red naming the divergence.
"""

import importlib.util
import json
import os

from conftest import REPO_ROOT, mk_fake_swift_toolchain, run_check

LEGACY = "checks/check_swift_coverage.py"


def _load_engine():
    spec = importlib.util.spec_from_file_location(
        "coverage_swift_parity",
        REPO_ROOT / "gates" / "coverage_swift.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_codecov_json(repo, filename, covered, total):
    p = repo / ".build" / "x" / "debug" / "codecov" / "a.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"data": [{"files": [
        {"filename": filename, "covered_lines": covered,
         "total_lines": total},
    ]}]}
    p.write_text(json.dumps(payload), encoding="utf-8")


def test_both_report_fifty_percent_and_agree(repo, tmp_path, monkeypatch, capsys):
    # One fixture, two measurement methods: conftest's fake toolchain ships
    # lib.swift with 10 lines, lines 6-10 uncovered (FAKE_SHOW), and the
    # export summary counts 10 total / 5 covered (FAKE_EXPORT).
    # NOTE: the toolchain gets its OWN root, not tmp_path: both the `repo`
    # fixture and mk_fake_swift_toolchain default to <root>/proj, and sharing
    # one dir would couple the git fixture to toolchain debris.
    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    bin_ = mk_fake_swift_toolchain(tool_root)
    proj = tool_root / "proj"
    lib = proj / "Sources" / "pkg" / "lib.swift"
    assert lib.exists()

    _write_codecov_json(repo, str(lib), 5, 10)

    for floor, expect_ok in ((40, True), (60, False)):
        legacy = run_check(repo, LEGACY, "--min", str(floor))
        assert (legacy.returncode == 0) is expect_ok, legacy.stdout + legacy.stderr
        assert "50.0%" in (legacy.stdout + legacy.stderr)

    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")
    engine = _load_engine()
    binary = (bin_ / "store" / "PkgTests.xctest" / "Contents" / "MacOS" / "PkgTests")
    profdata = bin_ / "store" / "codecov" / "default.profdata"
    for floor, expect_ok in ((40.0, True), (60.0, False)):
        rc = engine.process(str(binary), str(profdata), str(proj),
                            floor, "")
        out = capsys.readouterr().out
        assert (rc == 0) is expect_ok, out
        assert "50.00%" in out, out
