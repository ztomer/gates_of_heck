"""lib/gate_identity.py: a verdict's key carries everything besides the tree that decided it.

GOH.VERDICTKEY (found by the CIP.CACHING survey): a proven step was keyed on rustc, cargo, python
and swift only, so another shellcheck first on PATH answered "proven ... skipped"; and a land under
a hand-set GOH_LAND_GATE wrote the goh-landed line a full gate writes. One red test per input.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from test_proven import STEP, env, goh, proven, repo, runs  # noqa: F401  (fixtures)
from timing_bounds import patience

LIB = REPO_ROOT / "lib" / "gate_identity.py"
STUB = "#!/bin/sh\necho '{name} 99.99.99 (a stub)'\n"


def _key(root: Path, mode: str = "full", path_front: Path | None = None) -> str:
    e = hermetic_env(drop_git=True)
    if path_front is not None:
        e["PATH"] = f"{path_front}:{e['PATH']}"
    r = subprocess.run([sys.executable, str(LIB), "key", "--mode", mode, "--root", str(root)],
                       env=e, capture_output=True, text=True, timeout=patience(60),
                       stdin=subprocess.DEVNULL, check=True)  # fmt: skip
    return r.stdout.strip()


def _gates(tmp_path: Path) -> Path:
    root = tmp_path / "gates-root"
    (root / "gates").mkdir(parents=True)
    (root / "gates" / "structural.sh").write_text("#!/bin/sh\nexit 0\n")
    return root


def _stub(tmp_path: Path, name: str) -> Path:
    d = tmp_path / "stub-bin"
    d.mkdir(exist_ok=True)
    (d / name).write_text(STUB.format(name=name))
    (d / name).chmod(0o755)
    return d


@pytest.mark.parametrize("tool", ["shellcheck", "ruff", "cargo-llvm-cov", "xcodebuild"])
def test_another_tool_first_on_path_is_another_identity(tmp_path: Path, tool: str) -> None:
    root = _gates(tmp_path)
    assert _key(root) != _key(root, path_front=_stub(tmp_path, tool)), tool


def test_another_gate_mode_is_another_identity(tmp_path: Path) -> None:
    """A hand-set GOH_LAND_GATE=true is not the full gate."""
    root = _gates(tmp_path)
    assert _key(root, "full") == _key(root, "full")
    assert _key(root, "full") != _key(root, "true")


def test_an_edited_gate_script_is_another_identity(tmp_path: Path) -> None:
    root = _gates(tmp_path)
    before = _key(root)
    (root / "gates" / "structural.sh").write_text("#!/bin/sh\nexit 0  # edited\n")
    assert _key(root) != before
    (root / "gates" / "__pycache__").mkdir()
    (root / "gates" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    assert _key(root) == _key(root), "bytecode is no gate edit"


def test_a_proven_step_runs_again_under_another_shellcheck(goh, repo, env, tmp_path) -> None:  # noqa: F811
    """The survey's reproduction: the same step, the same tree, another shellcheck first on PATH."""
    assert proven(goh, repo, env).returncode == 0 and runs(env) == 1
    again = proven(goh, repo, env)
    assert runs(env) == 1, again.stdout + again.stderr
    stubbed = dict(env, PATH=f"{_stub(tmp_path, 'shellcheck')}:{env['PATH']}")
    third = proven(goh, repo, stubbed, STEP)
    assert third.returncode == 0, third.stdout + third.stderr
    assert runs(env) == 2, f"proven under another shellcheck: {third.stdout}"
