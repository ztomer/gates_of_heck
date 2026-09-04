"""swift_gate.sh's GOH_SWIFT_LINT_BASELINE ratchet.

The reconciler (gates/swift_lint_baseline.py) is unit-tested against canned
swiftlint JSON modeled byte-for-byte on probed 0.65.1 output (2026-08-25) —
the real binary is far too slow to put under every assertion. The gate WIRING
is proven end-to-end through the real bash script with a FAKE swiftlint shim
on PATH (the fake-toolchain pattern from test_coverage_gate.py), and one
integration test runs the REAL swiftlint against tests/fixtures/
swift_baseline_project — skipped where the binary is absent.

Ratchet semantics under test (probed from swiftlint --baseline, and the
contract ZoneTilerWM's make check-lint already ships):

  listed violation tolerated / NEW one fails naming it /
  vanished entry = nudge, never failure / baseline may only shrink.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT

GATE = REPO_ROOT / "gates" / "swift_gate.sh"
HELPER = REPO_ROOT / "gates" / "swift_lint_baseline.py"
FIXTURE_PROJECT = REPO_ROOT / "tests" / "fixtures" / "swift_baseline_project"


def _load_helper():
    spec = importlib.util.spec_from_file_location("swift_lint_baseline", HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Canned swiftlint JSON report — field names, capitalization ("Error") and
# absolute paths exactly as `swiftlint lint --reporter json` emits them.
def _report_entry(file: str, line: int, rule: str, reason: str,
                  severity: str = "Error", char: int = 1) -> dict:
    return {
        "character": char,
        "file": file,
        "line": line,
        "reason": reason,
        "rule_id": rule,
        "severity": severity,
        "type": "Line Length",
    }


def live_path(root: Path, name: str = "Sample.swift") -> str:
    """A violation path as the real reporter emits it: absolute, under root."""
    return str(root / "Sources" / name)
REASON_160 = "Line should be 120 characters or less; currently it has 160 characters"

BASELINE_ENTRY = {
    "text": "let aaa = " + "a" * 150,
    "violation": {
        "location": {"character": 1, "file": "Sources/Sample.swift", "line": 3},
        "reason": REASON_160,
        "ruleDescription": "Lines should not span too many characters.",
        "ruleIdentifier": "line_length",
        "ruleName": "Line Length",
        "severity": "warning",
    },
}


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HELPER), *args],
        capture_output=True, text=True,
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    return r


def write_baseline(root: Path, entries: list) -> Path:
    p = root / ".swiftlint-baseline.json"
    p.write_text(json.dumps(entries, indent=2) + "\n")
    return p


def write_report(root: Path, violations: list) -> Path:
    p = root / "report.json"
    p.write_text(json.dumps(violations) + "\n")
    return p


# ── unit: match-key normalization ────────────────────────────────────────────


def test_normalize_path_strips_file_url_scheme(root):
    m = _load_helper()
    target = root / "Sources" / "S.swift"
    # A file:// URL and its bare absolute form must land on one string.
    assert m.normalize_path(f"file://{target}", root) == \
        m.normalize_path(str(target), root)


def test_normalize_path_makes_baseline_and_report_forms_agree(root):
    # Report side: absolute. Baseline sides: repo-relative AND swiftlint's own
    # leading-slash-stripped writer form. All three must land on one string.
    m = _load_helper()
    abs_form = m.normalize_path(str(root / "Sources" / "S.swift"), root)
    rel_form = m.normalize_path("Sources/S.swift", root)
    stripped_form = m.normalize_path(
        str(root / "Sources" / "S.swift").lstrip("/"), root)
    assert abs_form == rel_form
    assert abs_form == stripped_form


def test_match_key_ignores_line_column_and_severity_but_not_reason(root):
    m = _load_helper()
    base = {"file": "Sources/S.swift", "ruleIdentifier": "r",
            "reason": "has 160", "line": 3, "character": 1, "severity": "warning"}
    moved = dict(base, line=99, character=40)
    errored = dict(base, severity="error")
    drifted = dict(base, reason="has 150")
    k = lambda v: m.match_key(v, root)  # noqa: E731
    assert k(base) == k(moved), "code motion must not orphan the baseline"
    assert k(base) == k(errored), "severity flip must not orphan the baseline"
    assert k(base) != k(drifted), "a changed reason is a different violation"


# ── unit + CLI: the ratchet itself ──────────────────────────────────────────


def test_listed_violation_tolerated_exit_zero(root):
    b = write_baseline(root, [BASELINE_ENTRY])
    r = write_report(root, [_report_entry(live_path(root), 3, "line_length", REASON_160)])
    got = run_cli("--baseline", str(b), "--report", str(r), "--root", str(root))
    assert got.returncode == 0, got.stdout + got.stderr


def test_new_violation_fails_naming_file_rule_reason(root):
    b = write_baseline(root, [BASELINE_ENTRY])
    new_reason = "Line should be 120 characters or less; currently it has 200 characters"
    r = write_report(root, [
        _report_entry(live_path(root), 3, "line_length", REASON_160),
        _report_entry(live_path(root, "Extra.swift"), 1, "line_length", new_reason),
    ])
    got = run_cli("--baseline", str(b), "--report", str(r), "--root", str(root))
    assert got.returncode == 1
    out = got.stdout + got.stderr
    assert "Extra.swift:1" in out
    assert "line_length" in out
    assert new_reason in out
    # The tolerated violation must not be re-reported as new anywhere.
    assert REASON_160 not in out


def test_identical_duplicate_tolerated_only_per_baseline_count(root):
    # Two byte-identical violations in one file (same key — line is NOT in it):
    # tolerated iff the baseline lists that many copies.
    b = write_baseline(root, [BASELINE_ENTRY])
    dup = _report_entry(live_path(root), 3, "line_length", REASON_160)
    dup2 = _report_entry(live_path(root), 7, "line_length", REASON_160)
    r = write_report(root, [dup, dup2])
    got = run_cli("--baseline", str(b), "--report", str(r), "--root", str(root))
    assert got.returncode == 1, "one copy beyond the baseline must fail"

    write_baseline(root, [BASELINE_ENTRY, BASELINE_ENTRY])
    got2 = run_cli("--baseline", str(b), "--report", str(r), "--root", str(root))
    assert got2.returncode == 0, got2.stdout + got2.stderr


def test_vanished_entry_is_a_nudge_not_a_failure(root):
    b = write_baseline(root, [BASELINE_ENTRY])
    r = write_report(root, [])  # debt fully burned down
    got = run_cli("--baseline", str(b), "--report", str(r), "--root", str(root))
    assert got.returncode == 0, got.stdout + got.stderr
    out = got.stdout + got.stderr
    assert "\u26a0" in out and "re-record" in out.lower()


def test_empty_everything_is_clean(root):
    b = write_baseline(root, [])
    r = write_report(root, [])
    got = run_cli("--baseline", str(b), "--report", str(r), "--root", str(root))
    assert got.returncode == 0


def test_unparseable_report_is_gate_error_naming_swiftlint_rc(root):
    b = write_baseline(root, [])
    r = root / "report.json"
    r.write_text("{not json")
    got = run_cli("--baseline", str(b), "--report", str(r),
                  "--root", str(root), "--swiftlint-rc", "70")
    assert got.returncode == 2
    assert "70" in (got.stdout + got.stderr)


def test_missing_baseline_is_gate_error(root):
    r = write_report(root, [])
    got = run_cli("--baseline", str(root / "nope.json"),
                  "--report", str(r), "--root", str(root))
    assert got.returncode == 2
    assert "nope.json" in (got.stdout + got.stderr)


# ── gate wiring through the REAL bash gate with a FAKE swiftlint ────────────


def _mk_fake_swiftlint(bin_dir: Path, canned_report: Path | None) -> None:
    """A PATH-shim swiftlint: logs its argv, emits the canned JSON when the
    gate asks for the reporter format, exits like swiftlint does on findings."""
    shim = bin_dir / "swiftlint"
    body = 'echo "$@" >> "$GOH_SHIM_LOG"\n'
    if canned_report is not None:
        body += f'case "$*" in *--reporter*) cat "{canned_report}"; exit 2 ;; esac\n'
    body += "exit 0\n"
    shim.write_text("#!/bin/bash\n" + body)
    shim.chmod(0o755)


def _mk_fake_swift(bin_dir: Path) -> None:
    shim = bin_dir / "swift"
    shim.write_text("#!/bin/bash\nexit 0\n")  # build/test stages pass
    shim.chmod(0o755)


def _run_gate(proj: Path, env_extra: dict | None = None):
    env = dict(os.environ)
    env.pop("GOH_SWIFT_LINT_BASELINE", None)
    env["GOH_SWIFT_COLD"] = "0"
    env.pop("GOH_SWIFT_COV_MIN", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["/bin/bash", str(GATE), str(proj)],
        cwd=proj, capture_output=True, text=True, env=env,
    )


@pytest.fixture
def spm_proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    (p / "Package.swift").write_text(
        '// swift-tools-version:5.9\nimport PackageDescription\n'
        'let package = Package(name: "x", targets: [])\n')
    return p


def _wired_env(spm_proj: Path, tmp_path: Path, **kw) -> dict:
    bin_dir = tmp_path / "shims"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "shim.log"
    log.touch()
    _mk_fake_swiftlint(bin_dir, kw.pop("canned", None))
    _mk_fake_swift(bin_dir)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}",
           "GOH_SHIM_LOG": str(log)}
    env.update(kw)
    return env


def test_gate_unset_env_keeps_plain_strict_invocation(spm_proj, tmp_path):
    env = _wired_env(spm_proj, tmp_path)
    r = _run_gate(spm_proj, env)
    assert r.returncode == 0, r.stdout + r.stderr
    argv = Path(env["GOH_SHIM_LOG"]).read_text()
    assert "--strict" in argv and "--reporter" not in argv, (
        f"unset baseline must keep bare strict linting, saw: {argv}")


def test_gate_set_env_runs_reporter_and_reconciles_green(spm_proj, tmp_path):
    canned = tmp_path / "canned.json"
    canned.write_text(json.dumps(
        [_report_entry(live_path(spm_proj), 3, "line_length", REASON_160)]))
    baseline = tmp_path / "b.json"
    baseline.write_text(json.dumps([BASELINE_ENTRY]))
    env = _wired_env(spm_proj, tmp_path, canned=canned,
                     GOH_SWIFT_LINT_BASELINE=str(baseline))
    r = _run_gate(spm_proj, env)
    assert r.returncode == 0, r.stdout + r.stderr
    argv = Path(env["GOH_SHIM_LOG"]).read_text()
    assert "--reporter" in argv and "--strict" in argv


def test_gate_new_violation_fails_the_whole_gate(spm_proj, tmp_path):
    canned = tmp_path / "canned.json"
    canned.write_text(json.dumps([
        _report_entry(live_path(spm_proj, "New.swift"), 9, "line_length",
         REASON_160)]))
    baseline = tmp_path / "b.json"
    baseline.write_text(json.dumps([]))
    env = _wired_env(spm_proj, tmp_path, canned=canned,
                     GOH_SWIFT_LINT_BASELINE=str(baseline))
    r = _run_gate(spm_proj, env)
    assert r.returncode != 0
    combined = r.stdout + r.stderr
    assert "New.swift:9" in combined, "gate must name the new violation"


def test_gate_baseline_path_pointing_nowhere_dies_naming_var(spm_proj, tmp_path):
    env = _wired_env(spm_proj, tmp_path,
                     GOH_SWIFT_LINT_BASELINE=str(tmp_path / "absent.json"))
    r = _run_gate(spm_proj, env)
    assert r.returncode != 0
    assert "GOH_SWIFT_LINT_BASELINE" in (r.stdout + r.stderr)


# ── real end-to-end (skipped without the actual toolchain) ──────────────────


def swiftlint_available() -> bool:
    return shutil.which("swiftlint") is not None


@pytest.mark.skipif(not swiftlint_available(), reason="swiftlint not installed")
@pytest.mark.slow  # ~28s of real swiftlint runs; fast loop uses -m "not slow"
def test_real_swiftlint_ratchet_green_then_red(tmp_path):
    proj = tmp_path / "proj"
    shutil.copytree(FIXTURE_PROJECT, proj)

    def record_baseline() -> None:
        subprocess.run(
            ["swiftlint", "lint", "--write-baseline", ".swiftlint-baseline.json",
             "--quiet"],
            cwd=proj, capture_output=True,
        )

    record_baseline()
    env = dict(os.environ)
    env["GOH_SWIFT_COLD"] = "0"
    env["GOH_SWIFT_LINT_BASELINE"] = str(proj / ".swiftlint-baseline.json")
    env.pop("GOH_SWIFT_COV_MIN", None)

    green = subprocess.run(["/bin/bash", str(GATE), str(proj)],
                           cwd=proj, capture_output=True, text=True, env=env)
    assert green.returncode == 0, green.stdout + green.stderr

    src = proj / "Sources" / "Sample.swift"
    lines = src.read_text().splitlines()
    lines.append('let ccc = "' + "c" * 150 + '"')  # a brand-new violation
    src.write_text("\n".join(lines) + "\n")

    red = subprocess.run(["/bin/bash", str(GATE), str(proj)],
                         cwd=proj, capture_output=True, text=True, env=env)
    assert red.returncode != 0
    combined = red.stdout + red.stderr
    # The new violation sits on the appended last line (1-based).
    assert f"Sample.swift:{len(lines)}" in combined, \
        "new violation must be named by file:line"

    # And shrinking the source below the baseline stays GREEN with a nudge.
    # The function stays (the test target compiles against it); only its long
    # lines go away.
    src.write_text(
        "func fixtureLines() -> (String, String) {\n"
        '    let aaa = "short"\n'
        '    let bbb = "short"\n'
        "    return (aaa, bbb)\n"
        "}\n")
    nudged = subprocess.run(["/bin/bash", str(GATE), str(proj)],
                            cwd=proj, capture_output=True, text=True, env=env)
    assert nudged.returncode == 0, nudged.stdout + nudged.stderr
    assert "re-record" in (nudged.stdout + nudged.stderr).lower()


def test_gate_script_and_helper_stay_under_the_500_line_cap():
    for f in (GATE, HELPER):
        n = len(f.read_text().splitlines())
        assert n <= 500, f"{f.name} is {n} lines (cap 500)"
