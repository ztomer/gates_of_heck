"""A land whose diff changes no Rust input reuses the Rust coverage measurement (GOH.RUSTCOVSKIP).

goh's coverage group runs its pytest suite against an INSTRUMENTED binary (GOH_COV_RUST_EXTERNAL),
so its record keyed on the whole tree and every land re-measured: GOH.XEXITDETAIL (Python and
shell) spent 896.62 s of a 21:37 land on it. The measurement is now keyed on its own inputs
(`gate_identity.py rust`: the Rust build inputs and the Rust gate's coverage scripts), and a tree
whose Rust is unchanged runs the tests plain. Both directions, on a real cargo workspace and the
real gates/_rust_proven.sh: a Python, test or README edit reuses; a crate source, the lockfile or a
coverage script measures; a failed, unrecorded or GOH_PROVEN=0 run never lends its verdict.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

pytestmark = pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")

# The real reuse function, with the two seams a fixture cannot be: the measurement and the plain run
# say which ran, and the binary match (goh's own build of these inputs) answers a fixed directory.
HARNESS = """
set -u
. "$ROOT/tui/lib.sh"
cargo_dir="$PWD"
. "$ROOT/gates/_rust_proven.sh"
rust_cov_plain_bins() { echo /plain/bin; }
measure() { echo MEASURED; return "${MEASURE_RC:-0}"; }
plain() { echo "PLAIN bins=$RUST_COV_PLAIN_BINS"; }
rust_coverage_reuse measure plain
"""


def _git(repo: Path, *args: str) -> None:
    env = hermetic_env(drop_git=True)
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env,
                   stdin=subprocess.DEVNULL)  # fmt: skip


def _write(repo: Path, rel: str, text: str, commit: bool = True) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if commit:
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", rel)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A goh-shaped repo: a workspace over crates/, a lockfile, Python beside it."""
    r = tmp_path / "repo"
    r.mkdir()
    (r / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/a"]\nresolver = "2"\n')
    (r / "crates/a/src").mkdir(parents=True)
    (r / "crates/a/Cargo.toml").write_text(
        '[package]\nname = "a"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    (r / "crates/a/src/lib.rs").write_text("pub fn f() -> u64 {\n    1\n}\n")
    (r / "lib").mkdir()
    (r / "lib/x.py").write_text("X = 1\n")
    (r / ".gitignore").write_text("target/\n")
    subprocess.run(["cargo", "generate-lockfile", "--offline"], cwd=r, check=True,
                   capture_output=True, stdin=subprocess.DEVNULL)  # fmt: skip
    fast_init(r)
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    return r


def _run(repo: Path, **env: str) -> str:
    e = hermetic_env(drop_git=True, ROOT=str(REPO_ROOT), GOH_COV_RUST_EXTERNAL="true", **env)
    e.pop("GOH_RESOLVED_BIN", None)
    r = subprocess.run(["bash", "-c", HARNESS], cwd=repo, env=e, capture_output=True, text=True,
                       timeout=patience(120), stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    out = r.stdout + r.stderr
    ran = [w for w in ("MEASURED", "PLAIN bins=/plain/bin") if w in out]
    assert len(ran) == 1, out
    return ran[0].split()[0]


def test_a_tree_whose_rust_is_unchanged_reuses_the_measurement(repo: Path) -> None:
    assert _run(repo) == "MEASURED"
    assert _run(repo) == "PLAIN"
    # The XEXITDETAIL shape: Python, a test, a doc -- none of the Rust gate's inputs.
    _write(repo, "lib/x.py", "X = 2\n")
    _write(repo, "tests/test_x.py", "def test_x():\n    pass\n")
    _write(repo, "README.md", "# repo\n")
    assert _run(repo) == "PLAIN"


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("crates/a/src/lib.rs", "pub fn f() -> u64 {\n    2\n}\n"),
        ("crates/a/src/new.rs", "// a new source\n"),
        ("Cargo.lock", None),
        ("rust-toolchain.toml", '[toolchain]\nchannel = "stable"\n'),
    ],
)
def test_a_rust_input_change_measures_again(repo: Path, rel: str, text: str | None) -> None:
    assert _run(repo) == "MEASURED"
    if text is None:
        text = (repo / rel).read_text() + "\n# an edit\n"
    _write(repo, rel, text)
    assert _run(repo) == "MEASURED"
    assert _run(repo) == "PLAIN"


def test_a_failed_measurement_is_never_reused(repo: Path) -> None:
    assert _run(repo, MEASURE_RC="1") == "MEASURED"
    assert _run(repo) == "MEASURED"


def test_a_dirty_tree_or_the_cache_off_measures(repo: Path) -> None:
    assert _run(repo) == "MEASURED"
    assert _run(repo, GOH_PROVEN="0") == "MEASURED"
    _write(repo, "lib/x.py", "X = 3\n", commit=False)
    assert _run(repo) == "MEASURED"


def test_a_member_outside_the_rust_inputs_always_measures(repo: Path) -> None:
    """A workspace member the stamp formula does not name could change without moving the key."""
    (repo / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/a", "other/b"]\nresolver = "2"\n'
    )
    _write(
        repo, "other/b/Cargo.toml", '[package]\nname = "b"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    _write(repo, "other/b/src/lib.rs", "pub fn g() {}\n")
    subprocess.run(["cargo", "generate-lockfile", "--offline"], cwd=repo, check=True,
                   capture_output=True, stdin=subprocess.DEVNULL)  # fmt: skip
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "b")
    assert _run(repo) == "MEASURED"
    assert _run(repo) == "MEASURED"


def test_the_coverage_scripts_come_from_their_sources() -> None:
    """The Rust gate's coverage scripts are read from what coverage_gate.sh sources, imports and
    runs, never a hand list -- and the shared harness the XEXITDETAIL land touched is not one."""
    import gate_identity

    scripts = set(gate_identity.rust_gate_scripts(REPO_ROOT))
    assert {"gates/rust_gate.sh", "gates/_rust_proven.sh", "gates/coverage_gate.sh",
            "gates/_coverage_rust.sh", "gates/lcov_merge.py", "gates/_cov_prune.py",
            "lib/bash5.sh"} <= scripts  # fmt: skip
    assert "lib/xexit.py" not in scripts and "tools/land.sh" not in scripts


def test_a_coverage_script_edit_moves_the_identity(repo: Path, tmp_path: Path) -> None:
    import gate_identity

    root = tmp_path / "goh"
    files = gate_identity.rust_gate_scripts(REPO_ROOT)
    for rel in [*files, "lib/xexit.py"]:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, root / rel)
    before = gate_identity.rust(repo, root)
    (root / "lib/xexit.py").write_text("# not the Rust gate's\n")
    assert gate_identity.rust(repo, root) == before
    p = root / "gates/_coverage_rust.sh"
    p.write_text(p.read_text() + "\n# an edit\n")
    assert gate_identity.rust(repo, root) != before
