"""Every proven step keys PYTHONPATH by CONTENT, the one formula the Rust coverage key uses.

GOH.PROVENPYPATH: gates/_proven.sh's live identity printed `env PYTHONPATH=<path>`, and goh's land
puts the tip's head export, ~/.cache/goh/head/<sha>, first on PYTHONPATH -- a new path for every
commit, so every proven group (crate, repo, structural, ...) was keyed by that path and none was
ever reused from one goh land to the next. GOH.RUSTCOVKEYSCOPE fixed it for the coverage key only;
both now ask lib/gate_identity.pythonpath, the proven steps with the gates' files as the scope.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT
from test_proven import STEP, _git, env, goh, repo, runs  # noqa: F401  (fixtures)
from test_proven_goh_content import _export
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_identity


def _prove(goh: Path, repo: Path, env: dict, pythonpath: str | None):  # noqa: F811
    """The step, by the gates at goh, under the PYTHONPATH a land would hand it (None: unset)."""
    e = {k: v for k, v in env.items() if k != "PYTHONPATH"}
    if pythonpath is not None:
        e["PYTHONPATH"] = pythonpath
    return subprocess.run(["bash", str(goh / "gates" / "proven.sh"), "--", STEP], cwd=repo, env=e,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=patience(120), check=False)  # fmt: skip


def _second_export(first: Path, tmp_path: Path) -> Path:
    """The same commit's export at another path: the next land's ~/.cache/goh/head/<sha>."""
    dest = tmp_path / "head2" / first.name
    shutil.copytree(first, dest, symlinks=True)
    return dest


def test_two_head_export_paths_of_one_content_share_a_proven_step(goh, repo, env, tmp_path):  # noqa: F811
    a = _export(goh, tmp_path)
    b = _second_export(a, tmp_path)
    first = _prove(goh, repo, env, f"{a}:/usr/lib/py")
    assert first.returncode == 0 and runs(env) == 1, first.stdout + first.stderr
    second = _prove(goh, repo, env, f"{b}:/usr/lib/py")
    assert second.returncode == 0, second.stdout + second.stderr
    assert runs(env) == 1, f"a second path of one content on PYTHONPATH re-ran: {second.stdout}"
    assert "proven on this tree" in second.stdout, second.stdout


def test_one_changed_imported_file_on_pythonpath_reruns(goh, repo, env, tmp_path):  # noqa: F811
    """Calibration of the reuse above: content keying still sees a file a gate imports, and a
    root that is no gates root is still keyed by its path."""
    a = _export(goh, tmp_path)
    b = _second_export(a, tmp_path)
    assert _prove(goh, repo, env, str(a)).returncode == 0 and runs(env) == 1
    with open(b / "tui" / "lib.py", "a") as f:
        f.write("# one byte more\n")
    again = _prove(goh, repo, env, str(b))
    assert again.returncode == 0 and runs(env) == 2, again.stdout + again.stderr
    other = _prove(goh, repo, env, f"{b}:/usr/lib/other")
    assert other.returncode == 0 and runs(env) == 3, other.stdout + other.stderr
    unset = _prove(goh, repo, env, None)
    assert unset.returncode == 0 and runs(env) == 4, unset.stdout + unset.stderr


def test_a_root_behind_another_is_keyed_only_by_what_the_one_ahead_lacks(goh, tmp_path):  # noqa: F811
    """The live checkout ~/.zshrc puts behind the land's export: a file the export also has is
    never imported from it; a gates file only it has still moves the key."""
    a = goh
    live = tmp_path / "live"
    shutil.copytree(a, live, symlinks=True)

    def key(pp: str) -> str:
        return gate_identity.pythonpath(pp, tmp_path / "repo", gate_identity.gate_files)

    base = key(f"{a}:{live}")
    assert base.endswith(":goh:shadowed"), base
    with open(live / "tui" / "lib.py", "a") as f:
        f.write("# shadowed by a's copy\n")
    assert key(f"{a}:{live}") == base
    (live / "lib" / "only_here.py").write_text("X = 1\n")
    assert key(f"{a}:{live}") != base
    # Outside the gates' files (docs/, tests/): never what a gate imports, so never keyed.
    alone = key(str(a))
    (a / "docs").mkdir()
    (a / "docs" / "note.md").write_text("prose\n")
    assert key(str(a)) == alone


def test_the_coverage_key_and_the_proven_identity_share_one_formula():
    """No second formula: both name gate_identity.pythonpath, each with its own reader's scope."""
    src = (REPO_ROOT / "lib" / "gate_identity.py").read_text()
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "pythonpath"]  # fmt: skip
    scopes = sorted(ast.unparse(c.args[2]) for c in calls)
    assert scopes == ["gate_files", "rust_gate_scripts"], scopes
    proven = (REPO_ROOT / "gates" / "_proven.sh").read_text()
    default = next(ln for ln in proven.splitlines() if ln.startswith("PROVEN_ENV_DEFAULT="))
    assert "PYTHONPATH" not in default, "PYTHONPATH keyed by path in the live half: " + default


# The top-level directories of this checkout that are NOT the gates' files (gate_identity.PARTS).
_OUTSIDE = {p.name for p in REPO_ROOT.iterdir() if p.is_dir() and p.name not in gate_identity.PARTS
            and not p.name.startswith(".")}  # fmt: skip


def test_no_gate_module_imports_outside_the_gates_files():
    """gate_files keys a gates root on PYTHONPATH by PARTS alone; that holds while no gate module
    imports a top-level package of the checkout outside them (tests, hooks, scripts, docs, ...)."""
    bad = []
    for part in gate_identity.PARTS:
        top = REPO_ROOT / part
        for py in top.rglob("*.py") if top.is_dir() else []:
            if "__pycache__" in py.parts:
                continue
            for n in ast.walk(ast.parse(py.read_text(encoding="utf-8", errors="replace"))):
                names = []
                if isinstance(n, ast.Import):
                    names = [a.name for a in n.names]
                elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                    names = [n.module]
                bad += [f"{py.relative_to(REPO_ROOT)}:{n.lineno} {m}" for m in names
                        if m.split(".")[0] in _OUTSIDE]  # fmt: skip
    assert {"tests", "docs"} <= _OUTSIDE, _OUTSIDE  # calibration: the walk has names to catch
    assert not bad, "a gate imports outside the keyed gates files: " + ", ".join(bad)
