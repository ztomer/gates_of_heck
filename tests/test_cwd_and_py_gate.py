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
        cwd=sub, capture_output=True, text=True,
    )
    assert r.returncode == 1
    assert "big.py" in r.stderr


@pytestmark_py
def test_py_gate_clean_package_passes(tmp_path):
    pkg = tmp_path / "proj"
    (pkg / "app").mkdir(parents=True)
    (pkg / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\n'
        "[tool.ruff]\nline-length = 100\n"
        "[tool.pytest.ini_options]\naddopts = \"\"\n"
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
        cwd=pkg, capture_output=True, text=True,
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
        cwd=pkg, capture_output=True, text=True,
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
        "[tool.pytest.ini_options]\naddopts = \"\"\n"
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
        cwd=pkg, capture_output=True, text=True,
        env={**dict(__import__("os").environ), "GOH_PY_COV_MIN": "100"},
    )
    assert r.returncode != 0, (
        f"a 100% floor passed a never-imported module: {r.stdout!r}"
    )
    assert "untested.py" in r.stdout + r.stderr, (
        "the gate must NAME the unmeasured module, not just miss the floor"
    )
