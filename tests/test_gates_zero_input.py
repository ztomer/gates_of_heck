"""Every gates/ entry point is accounted for on ZERO INPUTS (GOH.DIFFCOVEMPTY).

THE CLASS. A gate that reads nothing reports what a gate that read everything and found nothing
reports: gates/diff_cov.py, given an empty coverage parts directory, said "no changed executable
Rust line" and exited 0 over a change it never measured, and two lanes cited that green. The
checks/ gates are swept for this by checks/check_empty_scope.py (every check_* run in a skeleton
with no content); gates/ entry points take their input as an ARGUMENT (a parts directory, a report,
a file list), which that sweep cannot supply, so nothing asked them.

THE TABLE. Each `gates/*.py` that runs as a program is in ZERO_INPUT, one of:
  * Probe   -- run here on an empty or missing input; it must exit non-zero;
  * Pinned  -- a named test elsewhere already runs it on zero inputs and asserts the refusal;
  * Legit   -- passing over zero inputs is the true answer, with the reason, and the gate says the
               count it read (or reads no input set at all).
A new entry point not in the table is red, and so is an entry whose file or pinned test is gone:
the table cannot rot into permission (close-the-class).
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

GATES = REPO_ROOT / "gates"
# Read at run time (lib/declared_reads.py): every gates/ entry point, and the pinned tests.
TREE_READS = ("gates/*.py", "tests/*.py")


@dataclass(frozen=True)
class Probe:
    argv: tuple[
        str, ...
    ]  # after the gate's path; `{empty}` is an empty dir, `{missing}` a gone path


@dataclass(frozen=True)
class Pinned:
    test: str  # tests/FILE.py::test_name


@dataclass(frozen=True)
class Legit:
    reason: str


ZERO_INPUT: dict[str, Probe | Pinned | Legit] = {
    "diff_cov.py": Pinned(
        "tests/test_diff_cov.py::test_no_parts_with_a_changed_rust_line_is_refused_naming_what_is_missing"
    ),
    "lcov_merge.py": Pinned(
        "tests/test_lcov_merge.py::test_no_parts_at_all_reports_nothing_coverable"
    ),
    "ruff_ratchet.py": Pinned(
        "tests/test_ruff_ratchet.py::test_a_tree_with_no_python_file_is_no_pass"
    ),
    "coverage_swift.py": Pinned(
        "tests/test_gates_zero_input.py::test_coverage_swift_refuses_a_report_with_no_swift_row"
    ),
    "py_cov_floor.py": Probe(("90",)),  # run in an empty directory: no .coverage data
    "swift_lint_baseline.py": Probe(("--baseline", "{missing}", "--report", "{missing}")),
    "py_lint_ceiling.py": Legit(
        "its input is the commit's staged .py files: a commit staging none has nothing to lint, "
        "and its passing line names the count (`0 staged files within ...`)"
    ),
    "py_noqa_parity.py": Legit(
        "its input is the file list it is given: none is nothing to silence, and its passing line "
        "names the count (`(0 files)`)"
    ),
    "path_invariance.py": Legit(
        "it reads no input set: it runs ONE gate command at two paths and reports that command's "
        "verdict; no command is a usage error (exit 2)"
    ),
    "required_tools.py": Legit(
        "its subject is goh's own tool tables (gates/required_tools.tsv), shipped with it: a repo "
        "whose steps name no tool needs none, which is the true answer"
    ),
    "ruff_agree.py": Legit(
        "its question is whether the ruffs on this machine DISAGREE, and no ruff cannot disagree. "
        "Its one caller (tools/pytest.sh) runs --pin, which counts a missing ruff as off the pin"
    ),
    "ruff_house.py": Legit("it prints the house rule set, a constant: it reads no input"),
}


def _entry_points() -> list[str]:
    return sorted(
        p.name
        for p in GATES.glob("*.py")
        if not p.name.startswith("_")
        and re.search(r'^if __name__ == "__main__":', p.read_text(encoding="utf-8"), re.MULTILINE)
    )


def test_every_gates_entry_point_is_accounted_for_on_zero_inputs():
    missing = sorted(set(_entry_points()) - set(ZERO_INPUT))
    assert not missing, (
        f"gates/ entry points with no zero-input verdict: {missing} -- add a Probe (it must refuse "
        "an empty input), a Pinned test that proves the refusal, or a Legit reason"
    )


def test_no_entry_is_stale():
    gone = sorted(set(ZERO_INPUT) - set(_entry_points()))
    assert not gone, f"ZERO_INPUT names gates that are no entry point now, delete: {gone}"
    for name, verdict in ZERO_INPUT.items():
        if isinstance(verdict, Pinned):
            rel, test = verdict.test.split("::")
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert re.search(rf"^def {re.escape(test)}\(", text, re.MULTILINE), (
                f"{name}: {verdict.test} is gone"
            )
        if isinstance(verdict, Legit):
            assert len(verdict.reason) > 40, f"{name}: a Legit entry needs its reason"


@pytest.mark.parametrize("name", sorted(n for n, v in ZERO_INPUT.items() if isinstance(v, Probe)))
def test_a_probed_gate_refuses_zero_inputs(name: str, tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    subs = {"{empty}": str(empty), "{missing}": str(tmp_path / "missing")}
    probe = ZERO_INPUT[name]
    assert isinstance(probe, Probe)
    argv = [subs.get(a, a) for a in probe.argv]
    r = subprocess.run(
        [sys.executable, str(GATES / name), *argv], cwd=empty, capture_output=True, text=True,
        env=hermetic_env(PYTHONPATH=f"{REPO_ROOT}:{REPO_ROOT / 'lib'}"), timeout=patience(60),
        stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert r.returncode != 0, f"{name} passed over zero inputs: {r.stdout}{r.stderr}"


def test_coverage_swift_refuses_a_report_with_no_swift_row(monkeypatch, tmp_path: Path):
    spec = importlib.util.spec_from_file_location("coverage_swift", GATES / "coverage_swift.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "xccov_report", lambda _x: "App.app  0.00% (0/0)\n")  # no .swift row
    with pytest.raises(SystemExit) as exc:
        mod.xcresult_verdict(str(tmp_path / "r.xcresult"), 90.0, "")
    assert exc.value.code == 2
