"""Every hooks/claude module that imports a sibling finds it from its own directory.

GOH.ONEVERDICTTREE3's lane run went red on tests/test_state_bounds.py: hooks/claude/
estate_advisories.py imports its sibling `load_runs` but put only the goh root and lib/ on sys.path,
so it found the sibling only when an earlier test had left hooks/claude there -- order-dependent,
green one run and red the next. Each module is imported here in a fresh interpreter (`-I`: no
script directory, no cwd, no PYTHONPATH) from another directory, so nothing but its own lines can
put hooks/claude on the path. The modules are derived: every one with a top-level import of a
module hooks/claude holds.
"""

from __future__ import annotations

import ast
import subprocess
import sys

import pytest
from conftest import REPO_ROOT
from timing_bounds import patience

HOOKS = REPO_ROOT / "hooks" / "claude"
LOAD = (
    "import importlib.util, sys; p = sys.argv[1]; "
    "s = importlib.util.spec_from_file_location('probe', p); "
    "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)"
)


def _importers() -> list[str]:
    siblings = {p.stem for p in HOOKS.glob("*.py")}
    out = []
    for path in sorted(HOOKS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names.add(node.module.split(".")[0])
        if names & (siblings - {path.stem}):
            out.append(path.name)
    return out


def test_the_importers_are_found() -> None:
    assert {"agent_stalls.py", "estate_advisories.py"} <= set(_importers()), _importers()


@pytest.mark.parametrize("name", _importers())
def test_a_hook_imports_its_siblings_with_its_directory_off_the_path(name, tmp_path) -> None:
    done = subprocess.run([sys.executable, "-I", "-c", LOAD, str(HOOKS / name)], cwd=tmp_path,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=patience(60), check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr[-2000:]
