"""CWD-independence and py_gate smoke tests.

Phase 4 contract: gates resolve .gatesrc / tui/lib.sh from the GIT ROOT, so
they behave identically when invoked from a subdirectory. py_gate is exercised
end-to-end on a real mini package (ruff + pytest must be installed).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT, commit_all, run_gate, write

STRUCTURAL = "gates/structural.sh"

pytestmark_py = pytest.mark.skipif(
    shutil.which("ruff") is None or shutil.which("pytest") is None,
    reason="ruff/pytest not on PATH",
)


def test_structural_from_subdirectory_uses_root_gatesrc(repo):
    # Cap lives in the ROOT .gatesrc; a 200-line file staged from inside src/
    # must still trip it — proving .gatesrc resolved from git root, not CWD.
    (repo / ".gatesrc").write_text("GOH_MAX_LINES=5\n")
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    (sub / "big.py").write_text("\n" * 20)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / STRUCTURAL), "--staged"],
        cwd=sub,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1
    assert "big.py" in r.stderr


def _physical(path: Path) -> str:
    """`path` as the filesystem spells it, which is the only spelling coverage matches."""
    return subprocess.run(
        ["/bin/bash", "-c", 'cd "$1" && /bin/pwd -P', "_", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_py_gate_scopes_coverage_to_the_physical_path(repo, tmp_path):
    """`--cov` must carry the path AS THE FILESYSTEM SPELLS IT.

    coverage matches the scope as a string prefix against the paths it records, so on a
    case-insensitive filesystem a logical spelling that differs in case scopes every
    measured file out of the report — silently, since the files still execute. Measured
    2026-10-08 from scripts/: a shell whose PWD said `~/projects/scripts` for the repo at
    `~/Projects/scripts` gave `--cov=/Users/.../projects/scripts`, and `[run] patch =
    subprocess` dropped every CHILD's data with it — 8 shell suites reported "ran but
    its children measured none of" their modules and the floor came back 0.00% with 251
    tests passing. Red-proofed against the pre-fix `pwd`.

    The scope is read from the argv the gate BUILDS (the runner stubbed through
    GOH_PY_RUNNER), so this asserts the seam rather than whatever coverage is installed.
    The `repo` fixture supplies the git repo: initialising one by hand here would be a
    fourth in this file, and tests/test_suite_drift.py's ratchet exists to stop exactly
    that (it counts the phrase in prose too, which is how this sentence found out).
    """
    probe = repo / "CaseProbe"
    probe.write_text("x")
    if not (repo / "caseprobe").exists():
        pytest.skip("needs a case-insensitive filesystem to spell a path two ways")

    pkg = repo / "MixedCaseProj"
    pkg.mkdir()
    write(repo, "MixedCaseProj/pyproject.toml", '[project]\nname = "m"\nversion = "0"\n')
    write(repo, "MixedCaseProj/app.py", 'def add(a, b):\n    """Add."""\n    return a + b\n')

    # The gate is invoked with the OTHER spelling, from a cwd spelled that way too —
    # the shape a user's shell produces, and the shape that used to measure nothing.
    variant = str(pkg).replace("MixedCaseProj", "mixedcaseproj")

    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir()
    argv_log = tmp_path / "argv.log"  # OUTSIDE `repo`: the gate stamps that tree and
    # refuses a pass over one that moved mid-run, which is exactly what a log file
    # written into it would be.
    # GOH_PY_RUNNER is the gate's own seam for the interpreter it drives the steps with
    # (gates/py_gate.sh splits it into argv), so the stub sees the EXACT argv the gate
    # built, `pytest -q --cov=... ` included, without PATH games that `python3 -m` bypasses.
    runner = stub_dir / "runner.sh"
    runner.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> "{argv_log}"\n')
    runner.chmod(0o755)

    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "py_gate.sh"), str(repo), variant],
        cwd=variant,
        capture_output=True,
        text=True,
        env={
            **dict(__import__("os").environ),
            "GOH_PY_RUNNER": str(runner),
            "GOH_PY_COV_MIN": "70",
        },
    )
    assert r.returncode == 0, r.stdout + r.stderr

    logged = argv_log.read_text()
    scopes = [
        line.split("--cov=", 1)[1].split()[0] for line in logged.splitlines() if "--cov=" in line
    ]
    assert scopes, f"the gate built no coverage scope at all:\n{logged}"
    for scope in scopes:
        assert scope == _physical(pkg), (
            f"coverage scope {scope!r} is not how the filesystem spells the package "
            f"({_physical(pkg)!r}); on a case-insensitive filesystem that silently "
            f"excludes every measured file"
        )


@pytestmark_py
def test_py_gate_clean_package_passes(tmp_path):
    pkg = tmp_path / "proj"
    (pkg / "app").mkdir(parents=True)
    (pkg / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\n'
        "[tool.ruff]\nline-length = 100\n"
        '[tool.pytest.ini_options]\naddopts = ""\n'
    )
    (pkg / "app" / "__init__.py").write_text("")
    (pkg / "app" / "core.py").write_text(
        'def add(a: int, b: int) -> int:\n    """Add."""\n    return a + b\n'
    )
    (pkg / "tests").mkdir()
    (pkg / "tests" / "test_core.py").write_text(
        "from app.core import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    subprocess.run(["git", "-C", str(pkg), "init", "-q", "-b", "main"], check=True)

    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "py_gate.sh"), str(pkg)],
        cwd=pkg,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all python gates passed" in r.stdout
    # honest placeholder says WHY — and warns to STDERR (diagnostic stream),
    # never stdout (the pre-fix _tui_warn leaked it into program output)
    assert "no coverage floor" in r.stderr


@pytestmark_py
def test_py_gate_failing_test_blocks_with_output(tmp_path):
    pkg = tmp_path / "proj2"
    (pkg / "app").mkdir(parents=True)
    (pkg / "pyproject.toml").write_text('[project]\nname = "y"\nversion = "0"\n')
    (pkg / "app" / "__init__.py").write_text("")
    (pkg / "tests").mkdir()
    (pkg / "tests" / "t.py").write_text("def test_x():\n    assert 1 == 2\n")
    subprocess.run(["git", "-C", str(pkg), "init", "-q", "-b", "main"], check=True)

    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "py_gate.sh"), str(pkg)],
        cwd=pkg,
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert "assert 1 == 2" in r.stderr  # failing output printed, not buried


def pytest_cov_available() -> bool:
    try:
        import pytest_cov  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(not pytest_cov_available(), reason="pytest-cov not installed")
def test_py_gate_cov_floor_names_never_imported_modules(tmp_path):
    """The floor must see UNTESTED modules. Bare `--cov` (no source scope)
    only measures what got imported: a 0%-covered never-imported module is
    invisible and GOH_PY_COV_MIN=100 passes it. Red-proofed against the
    pre-fix gate, which exited 0 here."""
    pkg = tmp_path / "proj3"
    (pkg / "app").mkdir(parents=True)
    (pkg / "pyproject.toml").write_text(
        '[project]\nname = "z"\nversion = "0"\n'
        "[tool.ruff]\nline-length = 100\n"
        '[tool.pytest.ini_options]\naddopts = ""\n'
    )
    (pkg / "app" / "__init__.py").write_text("")
    (pkg / "app" / "core.py").write_text(
        'def add(a: int, b: int) -> int:\n    """Add."""\n    return a + b\n'
    )
    # Never imported by any test: with bare --cov it does not exist at all.
    (pkg / "app" / "untested.py").write_text(
        'def never(a: int) -> int:\n    """Never called."""\n    return a\n'
    )
    (pkg / "tests").mkdir()
    (pkg / "tests" / "test_core.py").write_text(
        "from app.core import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    subprocess.run(["git", "-C", str(pkg), "init", "-q", "-b", "main"], check=True)

    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "py_gate.sh"), str(pkg)],
        cwd=pkg,
        capture_output=True,
        text=True,
        env={**dict(__import__("os").environ), "GOH_PY_COV_MIN": "100"},
    )
    assert r.returncode != 0, f"a 100% floor passed a never-imported module: {r.stdout!r}"
    assert "untested.py" in r.stdout + r.stderr, (
        "the gate must NAME the unmeasured module, not just miss the floor"
    )
