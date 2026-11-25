"""A script run through a root variable is followed through a DECLARED root, or refuses the key.

GOH.RUNRULEROOTS. The run edge followed `$VAR/x.sh` only through $HERE, $_goh_d, $GOH_ROOT, $GOH_DIR
and $GOH, so `"$GOH_PY" -S "$PROVEN_GOH_ROOT/lib/gate_identity.py"` (gates/_proven.sh and three
siblings) was no edge of a proven step's closure: lib/gate_identity.py, lib/step_closure.py,
lib/kept_rewrite.py and lib/reuse_audit.py were outside every step's key. The coverage key read
the same paths through ANY variable and dropped one whose guess named no goh file without a word.
A consumer's external runner that sources `$GOH_DIR/lib/x.sh` keyed nothing of goh's.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _tree_clone import clone_tree
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_identity
import rust_cov_scripts
from source_closure import copy_closure

# The closures span goh's code parts: a change under any may move what these tests read.
TREE_READS = ("gates/", "lib/", "checks/", "tools/", "tui/", "scripts/")


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def test_a_run_through_any_declared_root_is_an_edge_of_the_proven_closure(tmp_path: Path) -> None:
    """$PROVEN_GOH_ROOT, a variable the script assigns from its own directory, and one aliased from
    a root (`ROOT="$HERE/.."`) all place the script they run."""
    root = _tree(tmp_path / "goh", {
        "gates/a.sh": (
            '#!/bin/bash\n"$GOH_PY" -S "$PROVEN_GOH_ROOT/gates/x.sh"\n'
            'D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"\nbash "$D/y.sh"\n'
            'ROOT="$HERE/.."\nbash "$ROOT/tools/z.sh"\n'
        ),
        "gates/x.sh": "#!/bin/bash\n", "gates/y.sh": "#!/bin/bash\n", "tools/z.sh": "#!/bin/bash\n",
    })  # fmt: skip
    found = gate_identity._closure({"gates/a.sh"}, root, root)
    assert {"gates/x.sh", "gates/y.sh", "tools/z.sh"} <= found, sorted(found)


def test_the_proven_runner_keys_the_scripts_it_runs_through_proven_goh_root() -> None:
    """The real tree: what gates/_proven.sh and its siblings run by $PROVEN_GOH_ROOT is keyed."""
    found = gate_identity._closure({"gates/local_ci.sh", "gates/proven.sh"}, REPO_ROOT, REPO_ROOT)
    want = {"lib/gate_identity.py", "lib/step_closure.py", "lib/kept_rewrite.py",
            "lib/reuse_audit.py"}  # fmt: skip
    assert not sorted(want - found), "run by $PROVEN_GOH_ROOT, outside the proven closure"


@pytest.fixture(scope="module")
def goh_copy(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("cov") / "goh"
    copy_closure(root, *gate_identity.rust_gate_scripts(REPO_ROOT), root=REPO_ROOT)
    return root


def _with(goh_copy: Path, tmp_path: Path, line: str, extra: dict[str, str] | None = None) -> Path:
    root = tmp_path / "goh"
    clone_tree(goh_copy, root)
    _tree(root, extra or {})
    with (root / "gates" / "_hash.sh").open("a") as fh:
        fh.write(line)
    return root


def test_a_coverage_script_run_through_proven_goh_root_is_keyed(goh_copy, tmp_path) -> None:
    root = _with(goh_copy, tmp_path, '"$GOH_PY" "$PROVEN_GOH_ROOT/gates/x.sh"\n',
                 {"gates/x.sh": "#!/bin/bash\n"})  # fmt: skip
    assert "gates/x.sh" in gate_identity.rust_gate_scripts(root)


@pytest.mark.parametrize(
    ("line", "names"),
    [
        ('bash "$SOMEROOT/gates/_common.sh"\n', r"\$SOMEROOT/gates/_common\.sh"),
        ('. "$SOMEROOT/gates/_common.sh"\n', r"\$SOMEROOT/gates/_common\.sh"),
        ('bash "$SOMEROOT/elsewhere/q.sh"\n', r"\$SOMEROOT/elsewhere/q\.sh"),
        (
            '"$GOH_PY" "$PROVEN_GOH_ROOT/lib/gone.py"\n',
            r"\$PROVEN_GOH_ROOT/lib/gone\.py names no file",
        ),
    ],
)
def test_a_script_through_no_declared_root_refuses_the_key(goh_copy, tmp_path, line, names) -> None:
    """Never a silent skip: a script through an undeclared root (whatever its tail names), or
    through a declared one to no file, has no key, so the measurement runs."""
    root = _with(goh_copy, tmp_path, line)
    with pytest.raises(LookupError, match=rf"gates/_hash\.sh: {names}"):
        gate_identity.rust_gate_scripts(root)


def test_an_external_runner_keys_the_goh_files_it_reaches_by_goh_dir(
    goh_copy, tmp_path, monkeypatch
) -> None:
    """GOH_COV_RUST_EXTERNAL's runner sources `$GOH_DIR/lib/h.sh` and runs `$GOH_DIR/gates/r.sh`:
    both, and what they source, are keyed, and an edit to one moves the identity."""
    goh = _with(goh_copy, tmp_path, "", {
        "lib/h.sh": '. "$(dirname "${BASH_SOURCE[0]}")/h2.sh"\n', "lib/h2.sh": "K=1\n",
        "gates/r.sh": "#!/bin/bash\n",
    })  # fmt: skip
    repo = _tree(tmp_path / "repo", {
        "tools/run.sh": '#!/bin/bash\n. "$GOH_DIR/lib/h.sh"\nbash "$GOH_DIR/gates/r.sh"\n',
        "Cargo.toml": '[workspace]\nmembers = ["crates/a"]\nresolver = "2"\n',
        "crates/a/Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\nedition = "2021"\n',
        "crates/a/src/lib.rs": "pub fn f() {}\n",
    })  # fmt: skip
    monkeypatch.setenv("GOH_DIR", str(goh))
    ext = "./tools/run.sh tests"
    assert rust_cov_scripts.runner_goh_files(repo, ext, goh) == [
        "gates/r.sh", "lib/h.sh", "lib/h2.sh"]  # fmt: skip
    before = gate_identity.rust(repo, goh, ext)
    (goh / "lib" / "h2.sh").write_text("K=2\n")
    assert gate_identity.rust(repo, goh, ext) != before
