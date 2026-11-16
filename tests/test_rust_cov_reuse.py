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
from source_closure import copy_closure
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
measured() { echo MEASURED; return "${MEASURE_RC:-0}"; }
plain() { echo "PLAIN bins=$RUST_COV_PLAIN_BINS"; }
# As .gatesrc sets it: a shell variable, not exported.
[ -n "${FLOOR:-}" ] && GOH_COV_FLOOR_RUST="$FLOOR"
rust_coverage_reuse measured plain
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
    _git(r, "commit", "-qm", "first")
    return r


def _run_out(repo: Path, **env: str) -> tuple[str, str]:
    e = hermetic_env(drop_git=True, ROOT=str(REPO_ROOT), GOH_COV_RUST_EXTERNAL="true", **env)
    e.pop("GOH_RESOLVED_BIN", None)
    r = subprocess.run(["bash", "-c", HARNESS], cwd=repo, env=e, capture_output=True, text=True,
                       timeout=patience(120), stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    out = r.stdout + r.stderr
    ran = [w for w in ("MEASURED", "PLAIN bins=/plain/bin") if w in out]
    assert len(ran) == 1, out
    return ran[0].split()[0], out


def _run(repo: Path, **env: str) -> str:
    return _run_out(repo, **env)[0]


def _decision(out: str) -> str:
    """The one `↔` decision line every run prints (GOH.RUSTCOVREUSE): a passing step shows it."""
    lines = [ln for ln in out.splitlines() if ln.lstrip().startswith("↔ [rust] coverage:")]
    assert len(lines) == 1, out
    return lines[0]


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
    copy_closure(root, *gate_identity.rust_gate_scripts(REPO_ROOT), "lib/xexit.py", root=REPO_ROOT)
    before = gate_identity.rust(repo, root)
    (root / "lib/xexit.py").write_text("# not the Rust gate's\n")
    assert gate_identity.rust(repo, root) == before
    p = root / "gates/_coverage_rust.sh"
    p.write_text(p.read_text() + "\n# an edit\n")
    assert gate_identity.rust(repo, root) != before


def test_a_push_worktree_at_another_path_reuses_the_measurement(repo: Path, tmp_path: Path) -> None:
    """Every land gates in a push worktree of its own (~/.cache/goh/push/wt-<hash>/wt), and the step
    named the workspace by that absolute path: the first land after GOH.RUSTCOVSKIP found no record
    and measured for 535.36 s (GOH.RUSTCOVREUSE). A worktree of the same commit elsewhere reuses."""
    assert _run(repo) == "MEASURED"
    other = tmp_path / "push" / "wt-elsewhere" / "wt"
    _git(repo, "worktree", "add", "--detach", "-q", str(other), "HEAD")
    assert _run(other) == "PLAIN"


def test_per_push_bookkeeping_does_not_void_the_measurement(repo: Path) -> None:
    """The push gate's marks are new temp files every push, and goh's own land runs the tip's head
    export (a new GOH_DIR per commit, keyed by its content): none of them is configuration."""
    a = {
        "GOH_STRUCTURAL_MARK": "/t/mark.1",
        "GOH_CI_STEPS_MARK": "/t/steps.1",
        "GOH_DIR": str(REPO_ROOT),
    }
    b = {
        "GOH_STRUCTURAL_MARK": "/t/mark.2",
        "GOH_CI_STEPS_MARK": "/t/steps.2",
        "GOH_DIR": str(REPO_ROOT) + "/.",
    }
    assert _run(repo, **a) == "MEASURED"
    assert _run(repo, **b) == "PLAIN"
    # A real setting still moves the key.
    assert _run(repo, **b, GOH_MAX_LINES="400") == "MEASURED"


def test_a_floor_set_in_gatesrc_is_part_of_the_key(repo: Path) -> None:
    """The plain run judges no floor, so a floor raised in .gatesrc (a shell variable, not exported)
    reusing the old measurement would pass a floor nobody measured against."""
    assert _run(repo, FLOOR="95") == "MEASURED"
    assert _run(repo, FLOOR="95") == "PLAIN"
    assert _run(repo, FLOOR="99") == "MEASURED"


def test_every_run_prints_its_decision_and_why(repo: Path) -> None:
    """A miss was silent: nothing said the reuse had even been looked for."""
    _, out = _run_out(repo)
    assert "measuring -- no measurement of this workspace is recorded yet" in _decision(out)
    _, out = _run_out(repo)
    assert "reused -- measured on these Rust inputs" in _decision(out)
    _, out = _run_out(repo, GOH_MAX_LINES="400")
    assert "measuring -- the last measurement" in _decision(out)
    assert "another GOH_* configuration" in _decision(out)
    _write(repo, "crates/a/src/lib.rs", "pub fn f() -> u64 {\n    3\n}\n")
    _, out = _run_out(repo)
    assert "on other Rust inputs" in _decision(out)
    _, out = _run_out(repo, GOH_PROVEN="0")
    assert "measuring -- the proven cache is off" in _decision(out)
    _write(repo, "lib/x.py", "X = 9\n", commit=False)
    _, out = _run_out(repo)
    assert "measuring -- the tree is not clean" in _decision(out)
