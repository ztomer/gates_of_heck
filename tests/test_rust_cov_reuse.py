"""A land whose diff changes no Rust input reuses the Rust coverage measurement (GOH.RUSTCOVSKIP).

goh's coverage group drives an INSTRUMENTED binary with its pytest suite (GOH_COV_RUST_EXTERNAL), so
its record keyed on the whole tree and every land re-measured (896.62 s of one). It is keyed on its
own inputs now, and a tree whose Rust is unchanged runs the tests plain. THREE LEVELS
(GOH.PYSLOWTESTS: each case was two to five whole runs of the gate, 555 s a suite). The DECISION --
reuse, measure, record -- runs the real gates/_rust_proven.sh on a real cargo workspace. The KEY is
`gate_identity.rust` in process. What the SHELL adds to it (`_rust_cov_inputs`: the GOH_COV_*
settings, the allowlist, the floors file, the arguments) and how a miss names what moved
(`_rust_cov_moved`) are one bash each, with an identity that answers at once.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from _tree_clone import from_template
from conftest import REPO_ROOT, hermetic_env
from source_closure import copy_closure
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_identity

pytestmark = pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")

# The real reuse function, with two seams: the measurement and the plain run say which ran, and the
# binary match (goh's own build of these inputs) answers a fixed directory.
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


def _git(repo: Path, *args: str) -> str:
    env = hermetic_env(drop_git=True)
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env,
                          stdin=subprocess.DEVNULL, text=True).stdout  # fmt: skip


def _write(repo: Path, rel: str, text: str, commit: bool = True) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if commit:
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", rel)


@pytest.fixture
def repo(tmp_path: Path, tmp_path_factory) -> Path:
    """A goh-shaped repo: a workspace over crates/, a lockfile, Python beside it."""
    return from_template(tmp_path_factory, "cov-reuse", _build_repo, tmp_path / "repo")


def _build_repo(r: Path) -> None:
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


# The shell half alone, its `$GOH_PY` a stub that prints its argv.
SHELL = HARNESS[: HARNESS.index("rust_cov_plain_bins()")]


def _shell(repo: Path, tmp_path: Path, script: str, identity: str = 'echo "argv $*"') -> str:
    stub = tmp_path / "stub-python"
    stub.write_text(f"#!/bin/sh\n{identity}\n")
    stub.chmod(0o755)
    e = hermetic_env(drop_git=True, ROOT=str(REPO_ROOT), GOH_COV_RUST_EXTERNAL="true")
    e = {k: v for k, v in e.items() if k not in ("GOH_RESOLVED_BIN", "PYTHONPATH")}
    argv = ["bash", "-c", f"{SHELL}GOH_PY={stub}\n{script}"]
    r = subprocess.run(argv, cwd=repo, env=e, text=True, capture_output=True, check=False,
                       timeout=patience(60), stdin=subprocess.DEVNULL)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def _inputs(repo: Path, tmp_path: Path, *settings: str) -> list[str]:
    # What `_rust_cov_inputs` prints under each of SETTINGS (shell, in a subshell of its own).
    each = [f"( {s}\n_rust_cov_inputs s || true )\nprintf '\\0'\n" for s in settings]
    return _shell(repo, tmp_path, "".join(each)).split("\0")[:-1]


def _moved(repo: Path, tmp_path: Path, then: str, now: str, trees: str = "'' ''") -> str:
    (tmp_path / "then").write_text(then + "\n")
    (tmp_path / "now").write_text(now + "\n")
    call = f'_rust_cov_moved "{tmp_path}/then" "$(cat "{tmp_path}/now")" {trees}'
    return _shell(repo, tmp_path, call).strip()


def _identity(repo: Path, external: str = "true", **kw) -> list[str]:
    return gate_identity.rust(repo, REPO_ROOT, external, **kw)


_SCRIPTS = functools.cache(gate_identity.rust_gate_scripts)  # 0.3 s a closure


@pytest.fixture
def no_tools(monkeypatch) -> None:
    monkeypatch.setattr(gate_identity, "tools", list)  # rustup and xcode-select processes
    monkeypatch.setattr(gate_identity, "rust_gate_scripts", _SCRIPTS)


def _differ(before: list[str], after: list[str]) -> list[str]:
    return sorted({ln.split(" ")[0] for ln in {*before} ^ {*after}})


def _decision(out: str) -> str:
    """The one `↔` decision line every run prints (GOH.RUSTCOVREUSE): a passing step shows it."""
    lines = [ln for ln in out.splitlines() if ln.lstrip().startswith("↔ [rust] coverage:")]
    assert len(lines) == 1, out
    return lines[0]


def test_a_tree_whose_rust_is_unchanged_reuses_the_measurement(repo: Path) -> None:
    ran, out = _run_out(repo)
    assert ran == "MEASURED"
    # A miss was silent: nothing said the reuse had even been looked for (GOH.RUSTCOVREUSE).
    assert "measuring -- no measurement of this workspace is recorded yet" in _decision(out)
    # The XEXITDETAIL shape: Python, a test, a doc -- none of the Rust gate's inputs.
    _write(repo, "lib/x.py", "X = 2\n", commit=False)
    _write(repo, "README.md", "# repo\n", commit=False)
    _write(repo, "tests/test_x.py", "def test_x():\n    pass\n")
    ran, out = _run_out(repo)
    assert ran == "PLAIN"
    assert "reused -- measured on these Rust inputs" in _decision(out)


def test_what_the_identity_says_on_stderr_is_no_input_of_the_key(
    repo: Path, tmp_path: Path
) -> None:
    """GOH.PREPARITY: under load a head export fails now and then, and gate_identity.py says so on
    stderr. The key read it too, so the re-check after a measurement saw "moved", recorded nothing,
    and the next run measured again (five of these tests red at load ~150). An interpreter that
    writes a different line to stderr on every run plays that warning here."""
    noisy = tmp_path / "noisy-python"
    noisy.write_text(
        f'#!/bin/bash\necho "noise $$ $RANDOM" >&2\nexec {shutil.which("python3")} "$@"\n'
    )
    noisy.chmod(0o755)
    assert _run(repo, GOH_PY=str(noisy)) == "MEASURED"
    assert _run(repo, GOH_PY=str(noisy)) == "PLAIN"


@pytest.mark.usefixtures("no_tools")
def test_a_rust_input_change_moves_the_key(repo: Path) -> None:
    before = _identity(repo)
    for rel, text in (
        ("crates/a/src/lib.rs", "pub fn f() -> u64 {\n    2\n}\n"),
        ("crates/a/src/new.rs", "// a new source\n"),
        ("Cargo.lock", (repo / "Cargo.lock").read_text() + "\n# an edit\n"),
        ("rust-toolchain.toml", '[toolchain]\nchannel = "stable"\n'),
    ):
        _write(repo, rel, text, commit=False)
        _git(repo, "add", "-A")
        after = _identity(repo)
        assert _differ(before, after) == ["inputs"], rel
        before = after
    (repo / "lib/x.py").write_text("X = 2\n")
    assert _identity(repo) == before


def test_a_failed_measurement_is_never_reused(repo: Path) -> None:
    assert _run(repo, MEASURE_RC="1") == "MEASURED"
    assert _run(repo) == "MEASURED"


def test_a_dirty_tree_or_the_cache_off_measures(repo: Path) -> None:
    ran, out = _run_out(repo, GOH_PROVEN="0")
    assert ran == "MEASURED" and "measuring -- the proven cache is off" in _decision(out)
    assert _run(repo) == "MEASURED"  # the run before recorded nothing
    _write(repo, "lib/x.py", "X = 3\n", commit=False)
    ran, out = _run_out(repo)
    assert ran == "MEASURED" and "measuring -- the tree is not clean" in _decision(out)


def test_a_member_outside_the_rust_inputs_has_no_key(repo: Path, tmp_path: Path) -> None:
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
    with pytest.raises(LookupError, match="outside the Rust inputs: other/b"):
        _identity(repo)
    # ... and no identity is no key: it measures, and records nothing.
    said = _shell(repo, tmp_path, "! rust_cov_key s", 'echo "outside the Rust inputs" >&2; exit 1')
    assert said == "no identity: outside the Rust inputs\n"


def test_the_coverage_scripts_come_from_their_sources() -> None:
    """Read from what rust_gate.sh sources, imports and runs, never a hand list. lib/xexit.py is one
    (GOH.COVKEYGAP): gates/_common.sh's goh_step judges the coverage step through it."""
    scripts = set(gate_identity.rust_gate_scripts(REPO_ROOT))
    assert {"gates/rust_gate.sh", "gates/_rust_proven.sh", "gates/coverage_gate.sh",
            "gates/_coverage_rust.sh", "gates/lcov_merge.py", "gates/_cov_prune.py",
            "gates/_lcov_floor_maps.py", "lib/bash5.sh", "gates/_proven.sh", "gates/_hash.sh",
            "gates/_common.sh", "lib/xexit.py"} <= scripts  # fmt: skip
    out = ("lib/self_match.py", "tools/land.sh", "docs/", "changelog/", "tests/")
    assert not [s for s in scripts if s.startswith(out)]


@pytest.mark.usefixtures("no_tools")
def test_a_coverage_script_edit_moves_the_identity(repo: Path, tmp_path: Path) -> None:
    root = tmp_path / "goh"
    copy_closure(root, *gate_identity.rust_gate_scripts(REPO_ROOT), "lib/self_match.py",
                 root=REPO_ROOT)  # fmt: skip
    before = gate_identity.rust(repo, root)
    (root / "lib/self_match.py").write_text("# not the Rust gate's\n")
    assert gate_identity.rust(repo, root) == before
    p = root / "gates/_coverage_rust.sh"
    p.write_text(p.read_text() + "\n# an edit\n")
    assert gate_identity.rust(repo, root) != before


def test_a_push_worktree_at_another_path_reuses_the_measurement(repo: Path, tmp_path: Path) -> None:
    """Every land gates in a push worktree of its own; a step naming it by that path found no
    record, 535.36 s (GOH.RUSTCOVREUSE). The same commit elsewhere reuses."""
    assert _run(repo) == "MEASURED"
    other = tmp_path / "push" / "wt-elsewhere" / "wt"
    _git(repo, "worktree", "add", "--detach", "-q", str(other), "HEAD")
    assert _run(other) == "PLAIN"


@pytest.mark.usefixtures("no_tools")
def test_per_push_bookkeeping_does_not_move_the_key(
    repo: Path, tmp_path: Path, monkeypatch
) -> None:
    """The push gate's marks are new temp files every push, a land's GOH_DIR a new export (keyed by
    its content): none of them is configuration."""
    keys = []
    for mark in ("1", "2"):
        monkeypatch.setenv("GOH_STRUCTURAL_MARK", f"/t/mark.{mark}")
        monkeypatch.setenv("GOH_CI_STEPS_MARK", f"/t/steps.{mark}")
        keys.append(_identity(repo))
    assert keys[0] == keys[1]
    here, spelled = _inputs(repo, tmp_path, 'export GOH_DIR="$ROOT"', 'export GOH_DIR="$ROOT/."')
    assert here == spelled and here.startswith("rust-coverage v3\nstep s\nargv "), here
    # A real setting still moves the key, and a miss names it (GOH.ONEVERDICTTREE).
    monkeypatch.setenv("GOH_MAX_LINES", "400")
    other = _identity(repo)
    assert _moved(repo, tmp_path, "\n".join(keys[0]), "\n".join(other)) == "GOH_MAX_LINES"


def test_what_the_shell_adds_to_the_key(repo: Path, tmp_path: Path) -> None:
    """A FLOOR set in .gatesrc (a shell variable, not exported): the plain run judges none, so a
    raised one reusing the old measurement would pass a floor nobody measured against. The diff-
    coverage ALLOWLIST (GOH.DIFFCOV) and the FLOORS FILE (GOH.CRATEFLOOR) decide the verdict, by
    content. PYTHONPATH is handed on as the gate has it: no argument when it has none."""
    floors, allow = tmp_path / "floors.json", tmp_path / "allow.json"
    floors.write_text('{"file_floor": 70}')
    allow.write_text('{"entries": []}')
    f, a = f"GOH_COV_FLOORS_JSON={floors}", f"GOH_COV_DIFF_ALLOW={allow}"
    edit = f"printf '{{\"file_floor\": 71}}' >{floors}; printf '{{\"entries\": [1]}}' >{allow}"
    plain, f95, again, f99, ff, al, ff2, al2, path, size = _inputs(
        repo, tmp_path, ":", "GOH_COV_FLOOR_RUST=95", "GOH_COV_FLOOR_RUST=95",
        "GOH_COV_FLOOR_RUST=99", f, a, f"{f}; {edit}", a, "export PYTHONPATH=/x:/y",
        "GOH_COV_CACHE=/elsewhere GOH_COV_RUST_JOBS=3",
    )  # fmt: skip
    assert "--external true\n" in plain and "--pythonpath" not in plain
    assert "--external true --pythonpath /x:/y\n" in path
    assert f95 == again and len({plain, f95, f99, ff, al, ff2, al2, path}) == 8
    assert size == plain  # where, how big, how wide: no verdict
    named = [_moved(repo, tmp_path, *pair) for pair in ((f95, f99), (ff, ff2))]
    assert named == ["GOH_COV_FLOOR_RUST", "the floors file (GOH_COV_FLOORS_JSON)"]
    (repo / "diffcov_allow.json").write_text("{}")  # the default allowlist, in the repo's own tree
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "allow")
    assert "\ndiffcov-allow " in _inputs(repo, tmp_path, ":")[0]


def test_a_miss_finds_the_record_beside_the_stores_directories(repo: Path) -> None:
    """The store holds directories too (made/, scope-extra/): a `grep "$dir"/*` exited 2 on them,
    rust_gate.sh's pipefail read that as no record, and three goh lands in a row said "no
    measurement of this workspace is recorded yet" (GOH.RUSTCOVKEYSCOPE). It names what moved."""
    assert _run(repo, FLOOR="95") == "MEASURED"
    store = repo / ".git" / "goh-proven"
    (store / "made").mkdir(exist_ok=True)
    (store / "made" / "x").write_text("a mark\n")
    _write(repo, "crates/a/src/lib.rs", "pub fn f() -> u64 {\n    4\n}\n")
    ran, out = _run_out(repo, FLOOR="95")
    assert ran == "MEASURED"
    assert "no measurement of this workspace is recorded yet" not in out
    moved = "was on other inputs: the Rust build inputs (crates/a/src/lib.rs) moved"
    assert moved in _decision(out)


def _gates(dest: Path, **edits: str) -> Path:
    """A gates root as PYTHONPATH sees one, its coverage scripts three files (`_scope`)."""
    measure = gate_identity.rust_cov_scripts.RUST_MEASURE
    for rel in ("lib/gate_identity.py", measure, "gates/_coverage_rust.sh", "gates/lcov_merge.py"):
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_text(edits.get(Path(rel).stem, f"{rel}\n"))
    return dest


def _scope(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in (root / "gates").iterdir())


def _key(repo: Path, *entries: Path) -> str:
    return gate_identity.pythonpath(":".join(map(str, entries)), repo, _scope)


def test_a_head_export_on_pythonpath_is_keyed_by_content(repo: Path, tmp_path: Path) -> None:
    """goh's land puts the tip's head export first on PYTHONPATH, and keyed by path every land
    measured again for 468-476 s (GOH.RUSTCOVKEYSCOPE). Two exports with the same coverage scripts
    are one input; other coverage scripts, or another directory on the path, another."""
    py, other = tmp_path / "py", tmp_path / "other"
    for d in (py, other):
        d.mkdir()
    a, b = (_gates(tmp_path / "head" / n) for n in "ab")
    c = _gates(tmp_path / "head" / "c", _coverage_rust="an edit\n")
    assert _key(repo, a, py) == _key(repo, b, py)
    assert len({_key(repo, *at) for at in ((a, py), (c, py), (c, other), (b, other))}) == 4
    then, now = (f"pythonpath {_key(repo, r, py)}" for r in (a, c))
    assert _moved(repo, tmp_path, then, now) == "PYTHONPATH (its gates roots by content)"


@pytest.mark.usefixtures("no_tools")
def test_the_key_reads_pythonpath_by_the_coverage_scripts(repo: Path, monkeypatch) -> None:
    """`rust` keys it through `pythonpath` in the coverage scripts' scope; none has no line."""
    asked = []
    monkeypatch.setattr(gate_identity, "pythonpath", lambda *a: asked.append(a) or "KEYED")
    assert "pythonpath KEYED" in _identity(repo, path="/x:/y")
    assert asked == [("/x:/y", repo, gate_identity.rust_gate_scripts)]
    assert _differ(_identity(repo), _identity(repo, path="/x")) == ["pythonpath"]


def test_a_gates_root_behind_another_on_pythonpath_is_shadowed(repo: Path, tmp_path: Path) -> None:
    """~/.zshrc puts the live checkout, main BEFORE the land, on PYTHONPATH behind the land's head
    export: a file the root ahead of it also has is never read from it; only the rest is keyed. An
    earlier export there, pruned under a running suite, is no input either: gone, it was keyed by
    its path (GOH.VERDICTCARRY's land, 5 red)."""
    a, old = _gates(tmp_path / "head" / "a"), _gates(tmp_path / "live")
    before = _key(repo, a, old)
    _gates(old, _coverage_rust="main moved\n")
    assert _key(repo, a, old) == before
    shutil.rmtree(old)
    assert _key(repo, a, old) == before
    _gates(old)
    # A coverage-closure file only the root behind has is read from it, so it is keyed.
    (a / "gates/lcov_merge.py").unlink()
    only_behind = _key(repo, a, old)
    _gates(old, _coverage_rust="main moved\n", lcov_merge="and again\n")
    assert len({before, only_behind, _key(repo, a, old)}) == 3


def test_more_copies_of_one_gates_and_their_docs_are_no_new_input(
    repo: Path, tmp_path: Path
) -> None:
    """GOH.VERDICTFREEINLINE's land measured again, 830 s, for one more copy of the gates on
    PYTHONPATH, and a line that keys no files named the whole tree's diff. A wholly shadowed root
    and a repeated entry are no input; docs/ and changelog/ are no coverage scripts (above); a
    real PYTHONPATH miss names no file."""
    py = tmp_path / "py"
    py.mkdir()
    a, b = (_gates(tmp_path / "head" / n) for n in "ab")
    assert _key(repo, b, a, py, b) == _key(repo, a, py)
    then_tree = _tree(repo)
    _write(repo, "changelog/GOH.Y.md", "prose\n")  # the tree moves, by no input
    c = _gates(tmp_path / "head" / "c", _coverage_rust="an edit\n")
    then, now = (f"pythonpath {_key(repo, r, py)}" for r in (a, c))
    named = _moved(repo, tmp_path, then, now, f"{then_tree} {_tree(repo)}")
    assert named == "PYTHONPATH (its gates roots by content)"


def _tree(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD^{tree}").strip()


@pytest.mark.usefixtures("no_tools")
def test_the_runner_is_an_input_and_the_suite_is_not(repo: Path, tmp_path: Path) -> None:
    """The external command runs the binary THROUGH a runner (goh's ./tools/pytest.sh), keyed with
    what it sources; the suite it runs (tests/) is not, by the owner's rule (GOH.RUSTCOVSKIP)."""
    ext = 'GOH_TEST_BIN="$X/goh" ./tools/run.sh tests'
    _write(repo, "tools/run.sh", '. "$(dirname "${BASH_SOURCE[0]}")/helper.sh"\n')
    _write(repo, "tools/helper.sh", "K=1\n")
    _write(repo, "tests/test_x.py", "def test_x():\n    pass\n")
    before, tree = _identity(repo, ext), _tree(repo)
    (repo / "tests/test_x.py").write_text("def test_x():\n    assert True\n")
    assert _identity(repo, ext) == before
    _write(repo, "tools/helper.sh", "K=2\n")
    after = _identity(repo, ext)
    assert _differ(before, after) == ["runner"]
    trees = f"{tree} {_tree(repo)}"
    named = _moved(repo, tmp_path, "\n".join(before), "\n".join(after), trees)
    assert named == "the suite's runner (tools/helper.sh)"


def test_goh_keys_its_runner_and_not_the_scripts_its_suite_exercises() -> None:
    """goh itself (.gatesrc GOH_COV_RUST_EXTERNAL): the runner is tools/pytest.sh and its closure;
    the lib/ file GOH.PGREPSELF changed, and every test, are in no keyed line; lib/xexit.py is."""
    ext = 'GOH_TEST_BIN="$GOH_COVERAGE_BIN_DIR/goh" ./tools/pytest.sh'
    runner = set(gate_identity.runner_files(REPO_ROOT, ext))
    assert "tools/pytest.sh" in runner and "lib/house_python.sh" in runner
    assert not [r for r in runner if r.startswith("tests/")]
    keyed = runner | set(gate_identity.rust_gate_scripts(REPO_ROOT))
    keyed |= set(gate_identity.build_inputs(REPO_ROOT))
    assert "lib/self_match.py" not in keyed and "lib/xexit.py" in keyed
