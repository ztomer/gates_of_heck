"""Every FAST check the push layer runs has a staged twin the commit layer runs (GOH.STAGEDPARITY).

House rule 17's class, found in nine repos by koffee_big's KB.GATEPARITY (8f415fd): the push ran
`ruff format` and `swiftlint --strict`, the commit ran neither, so a green commit had its push
refused -- twice in one koffee_big lane. koffee_big grew the twin by hand in its own gate.sh; the
fix is ONE place, here: gates/commit_gate.sh runs the staged twins for every repo.

The self-check is exhaustive, not a list of the tools we remembered. Every `goh_step` in a
push-layer language gate is CLASSIFIED below, by its label as written in the source:

  FAST  -> the twin script under gates/ that runs it on staged files. The twin must carry a step
           whose label starts with the push label, and commit_gate.sh must invoke the twin.
  OPEN  -> a fast check with no twin yet, with its reason: an honest ratchet entry.
  SLOW  -> a whole-tree check (build, tests, coverage, a manifest or lockfile read) that stays at
           push, with its reason.

A step added to a push gate and classified nowhere is red; a FAST step whose twin is missing, or
which commit_gate.sh never invokes, is red; an entry naming a step that no longer exists is red
(a stale entry is a rug, close-the-class). Red first: `test_a_missing_twin_is_red` deletes a twin
and a commit_gate call from a copy of the real tree and watches the check name it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from test_commit_gate_structural import _consumer, _git

PUSH_GATES = ("py_gate.sh", "swift_gate.sh", "rust_gate.sh")
PARTS = {"rust_gate.sh": ("rust_gate.sh", "_rust_coverage.sh")}  # a gate and the files it sources

# A twin is a script under gates/ whose step label starts with the push label, or a (script, label)
# pair naming the exact staged step: structural.sh's native Rust step judges the staged .rs files
# with all three of rust_gate.sh's source checks at once (crates/goh/src/steps_rust.rs).
RUST_TWIN = ("structural.sh", "rust source policies (staged)")
FAST: dict[tuple[str, str], str | tuple[str, str]] = {
    ("rust_gate.sh", "fmt"): RUST_TWIN,
    ("rust_gate.sh", "no #[allow] / #[expect]"): RUST_TWIN,
    ("rust_gate.sh", "no emptiness asserts"): RUST_TWIN,
    ("py_gate.sh", "ruff check"): "py_staged.sh",
    ("py_gate.sh", "ruff check under $GOH_PY_LINT_CEILINGS"): "py_staged.sh",
    ("py_gate.sh", "ruff format check"): "py_staged.sh",
    ("py_gate.sh", "noqa parity with $GOH_PY_NOQA_PARITY"): "py_staged.sh",
    ("swift_gate.sh", "swiftlint --strict"): "swift_staged.sh",
}

# Empty (GOH.RUSTTWINS, 2026-11-15): the three Rust entries it held were already twinned by
# structural.sh's staged Rust step (G1e), which this table could not name. New entries need reasons.
OPEN: dict[tuple[str, str], str] = {}

_W = "whole tree"
SLOW: dict[tuple[str, str], str] = {
    ("py_gate.sh", "pytest (measuring coverage)"): _W,
    ("py_gate.sh", "coverage >= ${GOH_PY_COV_MIN}% over every repo file"): _W,
    ("py_gate.sh", "pytest"): _W,
    ("swift_gate.sh", "build + tests (warnings as errors)"): _W,
    ("swift_gate.sh", "test + coverage"): _W,
    (
        "swift_gate.sh",
        "coverage >= ${GOH_SWIFT_COV_MIN}%${GOH_SWIFT_COV_FLOORS:+ + per-target floors}",
    ): _W,
    ("swift_gate.sh", "test"): _W,
    ("swift_gate.sh", "xcodebuild test (-scheme $GOH_SWIFT_SCHEME)"): _W,
    ("swift_gate.sh", "no compiler warnings in $warn_dirs"): "reads a cold build's log",
    ("rust_gate.sh", "the lockfile resolves --locked"): "lockfile",
    ("rust_gate.sh", "clippy (-D warnings, all targets, all features)"): "compiles the crate",
    ("rust_gate.sh", "clippy ($cfg)$via"): "compiles the crate",
    ("rust_gate.sh", "no unused dependencies"): "manifest against the whole crate",
    ("rust_gate.sh", "cargo lints (manifest)"): "manifest",
    ("rust_gate.sh", "dependency currency"): "registry read",
    (
        "rust_gate.sh",
        "no known advisory in $lock (cargo audit: warnings, unmaintained, unsound, yanked)",
    ): "lockfile",
    (
        "rust_gate.sh",
        "every advisory exemption in $cfg/.cargo/audit.toml has its reason and still fires",
    ): "lockfile",
    ("rust_gate.sh", "every shared advisory ruling still holds in $lock"): "lockfile",
    (
        "rust_gate.sh",
        "no raw cargo audit outside goh's advisory-db wrapper (GOH_CI_STEPS, tools/)",
    ): "the repo's sourced GOH_CI_STEPS and every tools/ script, whole",
    (
        "rust_gate.sh",
        "every Cargo.lock at its newest compatible releases (cargo update --dry-run)",
    ): "registry read",
    ("rust_gate.sh", "lint policy is inherited"): "manifest",
    ("rust_gate.sh", "coverage (floor ${GOH_COV_FLOOR_RUST}%)"): _W,
    ("rust_gate.sh", "coverage (per-target floors)"): _W,
    # GOH.RUSTCOVSKIP: the coverage run's tests, plain, when its measurement is reused or a land
    # measures none (GOH.COVOFFLAND); in gates/_rust_coverage.sh, which rust_gate.sh sources.
    ("rust_gate.sh", "cargo test (plain)"): _W,
    ("rust_gate.sh", "external (plain): $GOH_COV_RUST_EXTERNAL"): _W,
}

# `goh_step "label" cmd...` and `goh_step_in "$dir" "label" cmd...`, outside comments.
_STEP = re.compile(r'^\s*goh_step(_in\s+"[^"]*")?\s+"((?:[^"\\]|\\.)*)"', re.MULTILINE)


def step_labels(text: str) -> list[str]:
    return [m.group(2) for m in _STEP.finditer(text)]


def native_labels(gates: Path) -> list[str]:
    """The staged step labels structural.sh's native layer runs: each `let label = "..."` in a
    `steps_*.rs` that crates/goh/src/structural.rs registers with `step!(<module>::step(`."""
    src = gates.parent / "crates" / "goh" / "src"
    registry = (src / "structural.rs").read_text(encoding="utf-8") if src.is_dir() else ""
    labels: list[str] = []
    for module in re.findall(r"step!\((steps_\w+)::step\(", registry):
        path = src / f"{module}.rs"
        if path.is_file():
            labels += re.findall(r'let label = "([^"]+)"', path.read_text(encoding="utf-8"))
    return labels


def parity_faults(gates: Path) -> list[str]:
    """Every way the commit layer can lack a fast check the push layer runs, named; [] is green."""
    faults: list[str] = []
    commit_gate = (gates / "commit_gate.sh").read_text(encoding="utf-8")
    seen: set[tuple[str, str]] = set()
    for gate in PUSH_GATES:
        text = "".join((gates / f).read_text(encoding="utf-8") for f in PARTS.get(gate, (gate,)))
        for label in step_labels(text):
            key = (gate, label)
            seen.add(key)
            if key in SLOW or key in OPEN:
                continue
            twin = FAST.get(key)
            if twin is None:
                faults.append(f"{gate}: step {label!r} is classified nowhere (FAST, OPEN or SLOW)")
                continue
            if isinstance(twin, tuple):
                script, staged_label = twin
                if staged_label not in native_labels(gates):
                    faults.append(
                        f"{gate}: fast step {label!r}: {script} runs no staged step "
                        f"{staged_label!r}"
                    )
                if not re.search(rf'"\$HERE/{re.escape(script)}"', commit_gate):
                    faults.append(
                        f"{gate}: fast step {label!r}: commit_gate.sh never runs {script}"
                    )
                continue
            path = gates / twin
            if not path.is_file():
                faults.append(
                    f"{gate}: fast step {label!r} has no staged twin: gates/{twin} is missing"
                )
                continue
            if not any(t.startswith(label) for t in step_labels(path.read_text(encoding="utf-8"))):
                faults.append(f"{gate}: fast step {label!r} has no step in gates/{twin}")
            if not re.search(rf'"\$HERE/{re.escape(twin)}"', commit_gate):
                faults.append(
                    f"{gate}: fast step {label!r}: commit_gate.sh never runs gates/{twin}"
                )
    for key in sorted((FAST.keys() | OPEN.keys() | SLOW.keys()) - seen):
        faults.append(f"stale entry: {key[0]} has no step {key[1]!r}")
    return faults


def test_every_fast_push_check_has_a_staged_twin():
    assert parity_faults(REPO_ROOT / "gates") == []


def _copy_gates(tmp_path: Path) -> Path:
    gates = tmp_path / "gates"
    gates.mkdir()
    src = tmp_path / "crates" / "goh" / "src"
    src.mkdir(parents=True)
    for name in ("structural.rs", "steps_rust.rs"):
        shutil.copy(REPO_ROOT / "crates" / "goh" / "src" / name, src / name)
    for name in (
        *PUSH_GATES,
        *PARTS["rust_gate.sh"],
        "commit_gate.sh",
        "py_staged.sh",
        "swift_staged.sh",
    ):
        src = REPO_ROOT / "gates" / name
        if src.is_file():
            shutil.copy(src, gates / name)
    return gates


def test_a_native_twin_that_loses_its_step_is_red(tmp_path):
    """RUSTTWINS: the Rust source checks' twin is structural.sh's native staged step; unregister
    it from structural.rs and the three push steps are untwinned, by name."""
    gates = _copy_gates(tmp_path)
    reg = tmp_path / "crates" / "goh" / "src" / "structural.rs"
    reg.write_text(reg.read_text().replace("step!(steps_rust::step(", "drop!(steps_rust::step("))
    faults = parity_faults(gates)
    assert sum("runs no staged step 'rust source policies (staged)'" in f for f in faults) == 3


def test_a_missing_twin_is_red(tmp_path):
    gates = _copy_gates(tmp_path)
    (gates / "swift_staged.sh").unlink(missing_ok=True)
    faults = parity_faults(gates)
    assert any("swift_staged.sh is missing" in f for f in faults), faults


def test_a_twin_the_commit_gate_never_runs_is_red(tmp_path):
    gates = _copy_gates(tmp_path)
    cg = gates / "commit_gate.sh"
    cg.write_text(cg.read_text().replace('"$HERE/py_staged.sh"', '"$HERE/elsewhere.sh"'))
    faults = parity_faults(gates)
    assert any("never runs gates/py_staged.sh" in f for f in faults), faults


def test_an_unclassified_push_step_is_red(tmp_path):
    gates = _copy_gates(tmp_path)
    pg = gates / "py_gate.sh"
    pg.write_text(pg.read_text() + '\ngoh_step "mypy" mypy .\n')
    faults = parity_faults(gates)
    assert any("'mypy' is classified nowhere" in f for f in faults), faults


def test_a_stale_entry_is_red(tmp_path):
    gates = _copy_gates(tmp_path)
    pg = gates / "py_gate.sh"
    pg.write_text(pg.read_text().replace('"ruff format check"', '"ruff fmt"'))
    faults = parity_faults(gates)
    assert any("stale entry: py_gate.sh has no step 'ruff format check'" in f for f in faults), (
        faults
    )


# The behaviour the table promises, through the real stock pre-commit in a consumer whose own
# tools/gate.sh runs nothing but structural: the commit layer still refuses a staged lint finding.

GATE_STRUCTURAL_ONLY = (
    '#!/usr/bin/env bash\nset -euo pipefail\nexec bash "$GOH_DIR/gates/structural.sh" "$@"\n'
)
GATE_RUNS_PY_STAGED = (
    "#!/usr/bin/env bash\nset -euo pipefail\n"
    'bash "$GOH_DIR/gates/structural.sh" "$@"\nbash "$GOH_DIR/gates/py_staged.sh" .\n'
)


def _commit_file(repo: Path, rel: str, body: str) -> subprocess.CompletedProcess:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    _git(repo, "add", rel)
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "plant"],
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        capture_output=True,
        text=True,
        timeout=180,
        stdin=subprocess.DEVNULL,
        check=False,
    )


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")
def test_a_staged_format_drift_is_refused_at_commit(tmp_path):
    got = _commit_file(_consumer(tmp_path, GATE_STRUCTURAL_ONLY), "tool.py", "x   =   1\n")
    text = got.stdout + got.stderr
    assert got.returncode != 0, text
    assert "ruff format check (staged)" in text and "tool.py" in text, text


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")
def test_a_gate_that_runs_the_twin_itself_runs_it_once(tmp_path):
    got = _commit_file(_consumer(tmp_path, GATE_RUNS_PY_STAGED), "tool.py", "x = 1\n")
    text = got.stdout + got.stderr
    assert got.returncode == 0, text
    assert text.count("✓ ruff format check (staged)") == 1, text


@pytest.mark.skipif(shutil.which("swiftlint") is None, reason="swiftlint not on PATH")
def test_a_staged_swiftlint_finding_is_refused_at_commit(tmp_path):
    got = _commit_file(
        _consumer(tmp_path, GATE_STRUCTURAL_ONLY), "Sources/A.swift", "let value = 1 ;\n"
    )
    text = got.stdout + got.stderr
    assert got.returncode != 0, text
    assert "trailing_semicolon" in text, text


# twin_due once asked `git ls-files -- '*.py'`, every TRACKED .py: a data-only commit in a Python
# repo ran py_staged over nothing it changed (GOH.BAREINDEX). The twin is due for the STAGED set.
@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")
def test_a_data_only_commit_in_a_python_repo_skips_the_python_twin(tmp_path):
    repo = _consumer(tmp_path, GATE_STRUCTURAL_ONLY)
    first = _commit_file(repo, "tool.py", "x = 1\n")
    assert first.returncode == 0 and "python (staged)" in first.stdout + first.stderr, first
    got = _commit_file(repo, "queue.json", "{}\n")
    text = got.stdout + got.stderr
    assert got.returncode == 0, text
    # Not the bare word "ruff": goh's own notice about an uncommitted edit to a ruff-named file in
    # the gates checkout says it, and is no step (GOH.PARITYRUFFNAME). The twin's steps are named.
    assert "python (staged)" not in text and "ruff check" not in text, text
    assert "ruff format check" not in text, text
