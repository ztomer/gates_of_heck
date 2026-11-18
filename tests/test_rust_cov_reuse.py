"""A land whose diff changes no Rust input reuses the Rust coverage measurement (GOH.RUSTCOVSKIP).

goh's coverage group runs its pytest suite against an INSTRUMENTED binary (GOH_COV_RUST_EXTERNAL),
so its record keyed on the whole tree and every land re-measured: GOH.XEXITDETAIL (Python and
shell) spent 896.62 s of a 21:37 land on it. The measurement is now keyed on its own inputs
(`gate_identity.py rust`: the Rust build inputs and the Rust gate's coverage scripts), and a tree
whose Rust is unchanged runs the tests plain. Both directions, on a real cargo workspace and the
real gates/_rust_proven.sh: a Python, test or README edit reuses; a crate source, the lockfile, a
coverage script or the suite's runner measures; a head export at a new path on PYTHONPATH does not
(GOH.RUSTCOVKEYSCOPE); a failed, unrecorded or GOH_PROVEN=0 run never lends its verdict. A miss
names the input that moved.
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
set -euo pipefail   # as gates/rust_gate.sh runs it: a pipeline that errs anywhere fails
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
    e = hermetic_env(drop_git=True, ROOT=str(REPO_ROOT), **{"GOH_COV_RUST_EXTERNAL": "true", **env})
    e.pop("GOH_RESOLVED_BIN", None)
    if "PYTHONPATH" not in env:  # an input of the key: the test's own, never the caller's
        e.pop("PYTHONPATH", None)
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


def test_what_the_identity_says_on_stderr_is_no_input_of_the_key(
    repo: Path, tmp_path: Path
) -> None:
    """GOH.PREPARITY: under load a head export fails now and then, and gate_identity.py says so on
    stderr ("could not export ... running the working tree"). The key read its stderr too, so the
    re-check after a measurement saw "moved", recorded nothing, and the next run measured again
    where it reuses (VERDICTCARRY's land at load ~150: five of these tests red). An interpreter
    that writes a different line to stderr on every run plays that warning here."""
    noisy = tmp_path / "noisy-python"
    noisy.write_text(
        f'#!/bin/bash\necho "noise $$ $RANDOM" >&2\nexec {shutil.which("python3")} "$@"\n'
    )
    noisy.chmod(0o755)
    assert _run(repo, GOH_PY=str(noisy)) == "MEASURED"
    assert _run(repo, GOH_PY=str(noisy)) == "PLAIN"


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


def test_the_diff_coverage_allowlist_is_part_of_the_key(repo: Path) -> None:
    """GOH.DIFFCOV: the allowlist decides the coverage verdict (an exempted line, a stale entry),
    and lives outside the Rust inputs: an edit to it reused the old measurement unjudged."""
    _write(repo, "diffcov_allow.json", '{"entries": []}\n')
    assert _run(repo) == "MEASURED"
    assert _run(repo) == "PLAIN"
    _write(repo, "diffcov_allow.json", '{"entries": [{"file": "f", "text": "t", "reason": "r"}]}\n')
    assert _run(repo) == "MEASURED"


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
    assert "on other inputs: the Rust build inputs (crates/a/src/lib.rs) moved" in _decision(out)
    _, out = _run_out(repo, GOH_PROVEN="0")
    assert "measuring -- the proven cache is off" in _decision(out)
    _write(repo, "lib/x.py", "X = 9\n", commit=False)
    _, out = _run_out(repo)
    assert "measuring -- the tree is not clean" in _decision(out)


def test_a_miss_finds_the_record_beside_the_stores_directories(repo: Path) -> None:
    """The store holds directories too (made/, scope-extra/), and the miss read it with a `grep
    "$dir"/* | xargs ls -t` pipeline: grep exited 2 on them, rust_gate.sh's pipefail read that as
    no record, and three goh lands in a row said "no measurement of this workspace is recorded
    yet" with three records there (GOH.RUSTCOVKEYSCOPE). It names what moved, file by file."""
    assert _run(repo, FLOOR="95") == "MEASURED"
    store = repo / ".git" / "goh-proven"
    (store / "made").mkdir(exist_ok=True)
    (store / "made" / "x").write_text("a mark\n")
    _write(repo, "crates/a/src/lib.rs", "pub fn f() -> u64 {\n    4\n}\n")
    _, out = _run_out(repo, FLOOR="95")
    assert "no measurement of this workspace is recorded yet" not in out
    moved = "was on other inputs: the Rust build inputs (crates/a/src/lib.rs) moved"
    assert moved in _decision(out)
    _, out = _run_out(repo, FLOOR="99")
    assert "was on other inputs: GOH_COV_FLOOR_RUST moved" in _decision(out)


def _gates_copy(dest: Path) -> Path:
    """A gates root as a head export is one: the coverage scripts and the identity, at a path."""
    import gate_identity

    copy_closure(dest, *gate_identity.rust_gate_scripts(REPO_ROOT), "lib/gate_identity.py",
                 root=REPO_ROOT)  # fmt: skip
    return dest


def _real_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Two directories on PYTHONPATH that are no gates root: there, so keyed by path (an entry
    that is not there is no input, gate_identity.pythonpath)."""
    out = (tmp_path / "py", tmp_path / "other")
    for d in out:
        d.mkdir(exist_ok=True)
    return out


def test_a_head_export_on_pythonpath_is_keyed_by_content(repo: Path, tmp_path: Path) -> None:
    """goh's land puts the tip's head export, ~/.cache/goh/head/<sha>, first on PYTHONPATH, and the
    key named it by path: every land, lib/ or tests/ only, measured again for 468-476 s
    (GOH.RUSTCOVKEYSCOPE). Two exports with the same coverage scripts are one input; one whose
    coverage scripts differ, or another directory on the path, is another."""
    py, other = _real_dirs(tmp_path)
    a = _gates_copy(tmp_path / "head" / "a")
    b = _gates_copy(tmp_path / "head" / "b")
    assert _run(repo, PYTHONPATH=f"{a}:{py}") == "MEASURED"
    assert _run(repo, PYTHONPATH=f"{b}:{py}") == "PLAIN"
    c = _gates_copy(tmp_path / "head" / "c")
    edited = c / "gates/_coverage_rust.sh"
    edited.write_text(edited.read_text() + "\n# an edit\n")
    assert _run(repo, PYTHONPATH=f"{c}:{py}") == "MEASURED"
    assert _run(repo, PYTHONPATH=f"{c}:{other}") == "MEASURED"
    _, out = _run_out(repo, PYTHONPATH=f"{b}:{other}")
    assert "PYTHONPATH (its gates roots by content) moved" in _decision(out)


def test_a_gates_root_behind_another_on_pythonpath_is_shadowed(repo: Path, tmp_path: Path) -> None:
    """~/.zshrc puts the live checkout on PYTHONPATH behind the land's head export, and it is main
    BEFORE the land: keyed whole it moved the key once after every coverage-script change. A file
    the root ahead of it also has is never read from it; only the rest is keyed."""
    a = _gates_copy(tmp_path / "head" / "a")
    old = _gates_copy(tmp_path / "live")
    assert _run(repo, PYTHONPATH=f"{a}:{old}") == "MEASURED"
    edited = old / "gates/_coverage_rust.sh"
    edited.write_text(edited.read_text() + "\n# main moved\n")
    assert _run(repo, PYTHONPATH=f"{a}:{old}") == "PLAIN"
    # A coverage-closure file only the root behind has is read from it, so it is keyed.
    (a / "gates/lcov_merge.py").unlink()
    assert _run(repo, PYTHONPATH=f"{a}:{old}") == "MEASURED"


def test_a_pruned_export_behind_the_tip_is_no_new_input(repo: Path, tmp_path: Path) -> None:
    """A land's PYTHONPATH carries earlier head exports behind the tip's, and lib/head_export.py
    prunes the oldest whenever another land exports one: shadowed, it was no input; gone, it was
    keyed by its path, so a running suite saw "PYTHONPATH moved" between two runs of one test
    (GOH.VERDICTCARRY's land, 5 red) and a real land measured again for nothing."""
    a = _gates_copy(tmp_path / "head" / "a")
    old = _gates_copy(tmp_path / "head" / "old")
    assert _run(repo, PYTHONPATH=f"{a}:{old}") == "MEASURED"
    shutil.rmtree(old)
    assert _run(repo, PYTHONPATH=f"{a}:{old}") == "PLAIN"


def test_the_callers_pythonpath_is_no_input_of_these_runs(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each run's PYTHONPATH is the test's own (none, unless it names one): the caller's held the
    live checkout and a land's head exports, which move under a running suite."""
    live = _gates_copy(tmp_path / "live")
    monkeypatch.setenv("PYTHONPATH", str(live))
    assert _run(repo) == "MEASURED"
    edited = live / "gates/_coverage_rust.sh"
    edited.write_text(edited.read_text() + "\n# main moved under the suite\n")
    assert _run(repo) == "PLAIN"


def test_more_copies_of_one_gates_and_their_docs_are_no_new_input(
    repo: Path, tmp_path: Path
) -> None:
    """GOH.VERDICTFREEINLINE's land (tests, gates/verdict_free_keys.txt, docs, changelog) measured
    again, 830 s, and named docs/ and changelog/ as what moved in PYTHONPATH: that PYTHONPATH held
    one more copy of the same gates (`goh:shadowed` once more), and a line that keys no files named
    the whole tree's diff. A wholly shadowed root and a repeated entry are no input; a gates root's
    docs/ and changelog/ are not its coverage scripts; a real PYTHONPATH miss names no file."""
    py, _ = _real_dirs(tmp_path)
    a = _gates_copy(tmp_path / "head" / "a")
    b = _gates_copy(tmp_path / "head" / "b")
    for rel in ("docs/config.md", "changelog/GOH.X.md"):
        (b / rel).parent.mkdir(parents=True, exist_ok=True)
        (b / rel).write_text("prose\n")
    assert _run(repo, PYTHONPATH=f"{a}:{py}") == "MEASURED"
    _write(repo, "docs/note.md", "prose\n")  # the tree moves, by no input
    assert _run(repo, PYTHONPATH=f"{b}:{a}:{py}:{b}") == "PLAIN"
    c = _gates_copy(tmp_path / "head" / "c")
    edited = c / "gates/_coverage_rust.sh"
    edited.write_text(edited.read_text() + "\n# an edit\n")
    _write(repo, "changelog/GOH.Y.md", "prose\n")
    ran, out = _run_out(repo, PYTHONPATH=f"{c}:{py}")
    assert ran == "MEASURED", out
    decision = _decision(out)
    assert "PYTHONPATH (its gates roots by content) moved" in decision, decision
    assert "docs/" not in decision and "changelog/" not in decision, decision


def test_the_runner_is_an_input_and_the_suite_is_not(repo: Path) -> None:
    """The external command runs the binary THROUGH a runner (goh's ./tools/pytest.sh): what it
    selects and how it runs moves the number, so it and what it sources are keyed; the suite it
    runs (tests/) is not, by the owner's rule (GOH.RUSTCOVSKIP)."""
    ext = {"GOH_COV_RUST_EXTERNAL": 'GOH_TEST_BIN="$X/goh" ./tools/run.sh tests'}
    _write(repo, "tools/run.sh", '. "$(dirname "${BASH_SOURCE[0]}")/helper.sh"\n')
    _write(repo, "tools/helper.sh", "K=1\n")
    _write(repo, "tests/test_x.py", "def test_x():\n    pass\n")
    assert _run(repo, **ext) == "MEASURED"
    _write(repo, "tests/test_x.py", "def test_x():\n    assert True\n")
    assert _run(repo, **ext) == "PLAIN"
    _write(repo, "tools/helper.sh", "K=2\n")
    _, out = _run_out(repo, **ext)
    assert "measuring" in _decision(out)
    assert "the suite's runner (tools/helper.sh) moved" in _decision(out)
    assert _run(repo, **ext) == "PLAIN"


def test_goh_keys_its_runner_and_not_the_scripts_its_suite_exercises() -> None:
    """goh itself (.gatesrc GOH_COV_RUST_EXTERNAL): the runner is tools/pytest.sh and its closure;
    the lib/ file GOH.PGREPSELF changed, and every test, are in no keyed line."""
    import gate_identity

    ext = 'GOH_TEST_BIN="$GOH_COVERAGE_BIN_DIR/goh" ./tools/pytest.sh'
    runner = set(gate_identity.runner_files(REPO_ROOT, ext))
    assert "tools/pytest.sh" in runner and "lib/house_python.sh" in runner
    assert not [r for r in runner if r.startswith("tests/")]
    keyed = runner | set(gate_identity.rust_gate_scripts(REPO_ROOT))
    keyed |= set(gate_identity.build_inputs(REPO_ROOT))
    assert "lib/self_match.py" not in keyed and "lib/xexit.py" not in keyed
