"""rust_gate.sh proves each group once on ITS OWN inputs (BACKLOG P3), and the record cannot lie.

The proven cache keyed every step on the whole tree: one README line re-gated all 29 of
a private server workspace's crates. Now the crate group is keyed on the crate, the packages it
reaches by `path =`, and the config files it reads; the repo-wide scans on the whole tree. Each way
a hit could be wrong is planted here on REAL cargo crates:

* an edit to a crate re-gates it and nothing else; an edit to a path dependency re-gates its users;
* an edit outside every crate (a README) re-gates nothing; an edit to shared config re-gates all;
* a source reaching outside its crate at run time widens that crate's scope to the whole tree;
* a build that READ a file outside the scope (dep-info) is never recorded;
* GOH_PROVEN=0 runs everything.

The GATE itself (`_gate`) runs where it is the subject: both groups through one cache, and
the compiler's own dep-info. What a scope keys and re-gates is
gates/_rust_proven.sh's, run beside the real crates with a group that says it ran (`_groups`,
GOH.PYSLOWTESTS: 40 whole gates, 396 s of a run).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import _fake_cargo
import pytest
from _fast_git import fast_init
from _gatesrc_kit import write_gatesrc
from _tree_clone import from_template
from conftest import hermetic_env
from timing_bounds import patience

ROOT = Path(__file__).resolve().parent.parent

# NOT an xdist group: every test builds its own crates in its own temp repo, records proofs in that
# repo's own git dir and runs the session's binary, so nothing here is shared or mutable.
pytestmark = [
    pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed"),
]

LIB = "pub fn f() -> u64 {\n    1\n}\n"


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
    ).stdout


def _crate(repo: Path, name: str, deps: str = "", lib: str = LIB) -> None:
    d = repo / "crates" / name
    (d / "src").mkdir(parents=True)
    (d / "Cargo.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n{deps}'
    )
    (d / "src" / "lib.rs").write_text(lib)


@pytest.fixture(scope="module")
def gates() -> Path:
    """A FROZEN clean clone of today's gates. The proven key includes the gates checkout's identity
    (its diff and untracked files), and other tests of a parallel suite briefly create files in
    the live one -- which the cache rightly refuses to trust, and which made this suite flaky."""
    from _todays_gates import todays_gates_read

    return todays_gates_read()


@pytest.fixture(autouse=True)
def _native(goh: Path, gates: Path, monkeypatch) -> None:
    """The session's own goh build (conftest), never cargo's target path: another test may be
    re-linking that while this one reads it -- the race conftest's fixture exists to close."""
    monkeypatch.setenv("SCOPED_CACHE_GOH", str(goh))
    monkeypatch.setenv("SCOPED_CACHE_GATES", str(gates))


@pytest.fixture
def estate(tmp_path: Path, tmp_path_factory) -> Path:
    """crates/a uses crates/b by path; crates/c stands alone. Each is its own workspace. Built once
    per worker, cloned per test (GOH.FIXTURECOST)."""
    return from_template(tmp_path_factory, "scoped-cache", _build_estate, tmp_path / "repo")


def _build_estate(repo: Path) -> None:
    repo.mkdir()
    _crate(repo, "b")
    _crate(repo, "a", deps='b = { path = "../b" }\n', lib="pub fn g() -> u64 {\n    b::f()\n}\n")
    _crate(repo, "c")
    (repo / "README.md").write_text("# estate\n")
    write_gatesrc(repo, "GOH_DEPS_OFFLINE=1\n")
    (repo / ".gitignore").write_text("target/\n.build/\n")
    for name in ("a", "b", "c"):
        subprocess.run(
            ["cargo", "generate-lockfile", "--offline"],
            cwd=repo / "crates" / name,
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
        )
    fast_init(repo)
    _git(repo, "add", "-A")


@pytest.fixture
def fake_cargo(tmp_path: Path, monkeypatch) -> Path:
    """The recorded fake (tests/_fake_cargo.py) for a test of the cache's SCOPING: what it keys and
    re-gates is decided by cargo's metadata, never by a compile. Returns the call log."""
    bin_dir, log = _fake_cargo.install(tmp_path / "fake-cargo")
    monkeypatch.setenv("SCOPED_CACHE_FAKE_CARGO", str(bin_dir))
    return log


# The two groups as gates/rust_gate.sh runs them, each a line that says it ran; the crate group does
# CRATE_DOES, in the crate. The gates' own identity (two closures of goh, 0.6 s each) answers at
# once: it is frozen here, and tests/test_rust_gate_floor.py's subject.
GROUPS = """
set -euo pipefail
repo="$1" cargo_dir="$2"
cd "$repo"
. "$GOH_DIR/gates/_common.sh"
. ./.gatesrc
goh_init rust
. "$GOH_DIR/gates/_rust_proven.sh"
GOH_PY="$STUB"
goh_resolve_native
crate_group() { echo "RAN crate"; (cd "$cargo_dir" && eval "${CRATE_DOES:-:}"); }
repo_group() { echo "RAN repo"; }
rust_proven_group crate compile crate_group
rust_proven_group repo tree repo_group
goh_done
"""
STUB = """#!/bin/bash
case "$*" in *rust_runner_id.py*|*"gate_identity.py proven"*) echo "the gates"; exit 0 ;; esac
exec "$REAL_PY" "$@"
"""


def _groups(repo: Path, crate: str, **env: str) -> subprocess.CompletedProcess:
    stub = repo.parent / "stub-python"
    stub.write_text(STUB)
    stub.chmod(0o755)
    gates = os.environ["SCOPED_CACHE_GATES"]
    full = hermetic_env(drop_git=True, GOH_DIR=gates, PYTHONPATH=gates, STUB=str(stub),
                        REAL_PY=sys.executable, GOH_BIN=os.environ["SCOPED_CACHE_GOH"],
                        **env)  # fmt: skip
    argv = ["bash", "-c", GROUPS, "groups", str(repo), str(repo / "crates" / crate)]
    r = subprocess.run(argv, cwd=repo, capture_output=True, text=True, env=full, check=False,
                       timeout=patience(120), stdin=subprocess.DEVNULL)  # fmt: skip
    for group in ("crate", "repo"):  # it ran, or it said why not: never both, never neither
        if r.returncode == 0:
            assert _skipped(r, group) != (f"RAN {group}" in r.stdout), r.stdout + r.stderr
    return r


def _gate(repo: Path, crate: str, **env: str) -> subprocess.CompletedProcess:
    gates = Path(os.environ["SCOPED_CACHE_GATES"])
    # The frozen clone is PYTHONPATH too, never the live checkout the suite's PYTHONPATH names:
    # every proven key holds each gates root on PYTHONPATH by content (lib/gate_identity.py
    # `pythonpath`), and a new file another test of the parallel suite briefly made in the live
    # lib/ missed the second run of one tree (GOH.FLAKYSCOPEDCACHE; test_proven.py sets both too).
    full = hermetic_env(drop_git=True, GOH_DIR=str(gates), PYTHONPATH=str(gates))
    full.update(GOH_BIN=os.environ["SCOPED_CACHE_GOH"], **env)
    if fake := os.environ.get("SCOPED_CACHE_FAKE_CARGO"):
        full["PATH"] = f"{fake}:{full['PATH']}"
    return subprocess.run(
        ["bash", str(gates / "gates" / "rust_gate.sh"), str(repo), str(repo / "crates" / crate)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=full,
        timeout=patience(120),
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _skipped(r: subprocess.CompletedProcess, group: str) -> bool:
    return f"{group} checks: proven on these inputs" in r.stdout + r.stderr


def _edit(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.write_text(path.read_text() + text)
    _git(repo, "add", "-A")


def test_the_gate_runs_each_group_once_on_one_tree(estate: Path, fake_cargo: Path) -> None:
    """gates/rust_gate.sh itself: the first run proves both groups, the second skips both; the
    log says what RAN, not what the gate printed (clippy, once); GOH_PROVEN=0 runs everything."""
    first = _gate(estate, "c")
    assert first.returncode == 0, first.stdout + first.stderr
    assert not _skipped(first, "crate") and not _skipped(first, "repo"), first.stdout
    again = _gate(estate, "c")
    assert _skipped(again, "crate") and _skipped(again, "repo"), again.stdout + again.stderr
    assert [Path(d).name for d in _fake_cargo.calls(fake_cargo, "clippy")] == ["c"]
    off = _gate(estate, "c", GOH_PROVEN="0")
    assert not _skipped(off, "crate") and not _skipped(off, "repo"), off.stdout
    assert [Path(d).name for d in _fake_cargo.calls(fake_cargo, "clippy")] == ["c", "c"]


def test_only_the_edited_crate_and_its_users_are_re_gated(estate: Path) -> None:
    for name in ("a", "b", "c"):
        first = _groups(estate, name)
        assert first.returncode == 0, first.stdout + first.stderr
        assert not _skipped(first, "crate"), name
        # The repo-wide scans read the whole repo whichever crate called them: the first crate
        # records them and every later crate of the same tree finds the record.
        assert _skipped(first, "repo") == (name != "a"), (name, first.stdout + first.stderr)
    _edit(estate, "crates/b/src/lib.rs", "pub fn h() -> u64 {\n    2\n}\n")
    after = {name: _groups(estate, name) for name in ("a", "b", "c")}
    assert all(r.returncode == 0 for r in after.values()), {
        n: r.stdout + r.stderr for n, r in after.items() if r.returncode
    }
    assert not _skipped(after["b"], "crate"), "the edited crate was not re-gated"
    assert not _skipped(after["a"], "crate"), (
        "a user of the edited path dependency was not re-gated"
    )
    assert _skipped(after["c"], "crate"), "an unrelated crate was re-gated"


def test_an_edit_outside_every_crate_re_gates_no_crate_but_shared_config_re_gates_all(
    estate: Path,
) -> None:
    assert _groups(estate, "c").returncode == 0
    _edit(estate, "README.md", "more prose\n")
    readme = _groups(estate, "c")
    assert _skipped(readme, "crate"), readme.stdout + readme.stderr
    assert not _skipped(readme, "repo"), "the repo-wide scans are keyed on the whole tree"
    (estate / "clippy.toml").write_text("too-many-arguments-threshold = 9\n")
    _git(estate, "add", "-A")
    config = _groups(estate, "c")
    assert not _skipped(config, "crate"), "a new clippy.toml above the crate did not re-gate it"


def test_a_resolvable_reach_outside_the_crate_keys_on_exactly_that_file(
    estate: Path,
) -> None:
    """An include of `../../shared/fixture.json` (relative to its source) is keyed on that file:
    editing it re-gates the crate, a README edit does not. Measured 2026-10-05: every crate of a
    private server workspace has include_str!("../../../VERSION"), and keying them on the whole tree
    for it made the cache worthless. (An include: the crate group compiles and runs no test, so a
    path read at run time is not its input -- GOH.GATEFLOOR, tests/test_rust_gate_floor.py.)"""
    (estate / "crates" / "shared").mkdir()
    (estate / "crates" / "shared" / "fixture.json").write_text("{}\n")
    _edit(
        estate,
        "crates/c/src/lib.rs",
        '\npub const FIXTURE: &str = include_str!("../../shared/fixture.json");\n',
    )
    assert _groups(estate, "c").returncode == 0
    _edit(estate, "README.md", "more prose\n")
    assert _skipped(_groups(estate, "c"), "crate"), "a README edit re-gated a precisely keyed crate"
    _edit(estate, "crates/shared/fixture.json", "\n")
    assert not _skipped(_groups(estate, "c"), "crate"), "editing the file it reads did not re-gate"


def test_an_unnameable_reach_keys_the_crate_on_the_whole_tree_only_where_it_compiles(
    estate: Path,
) -> None:
    """A `..` join read at RUN time keys the crate's tests on the whole tree (`goh rust-scope`,
    tests/test_rust_gate_floor.py), never the crate group, which compiles and runs none: a README
    edit re-gates nothing there (GOH.GATEFLOOR). One in a build script runs at compile time: the
    whole tree again."""
    _edit(
        estate,
        "crates/c/src/lib.rs",
        "\npub fn up() -> std::path::PathBuf {\n"
        '    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..")\n'
        "}\n",
    )
    assert _groups(estate, "c").returncode == 0
    _edit(estate, "README.md", "more prose\n")
    assert _skipped(_groups(estate, "c"), "crate"), "a run-time reach widened the compile scope"
    (estate / "crates" / "c" / "build.rs").write_text(
        'fn main() {\n    let _ = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..");\n}\n'
    )
    _git(estate, "add", "-A")
    assert _groups(estate, "c").returncode == 0
    _edit(estate, "README.md", "more prose\n")
    assert not _skipped(_groups(estate, "c"), "crate"), (
        "a build script's `..` was keyed on the crate"
    )


def test_a_path_dependencys_own_test_reads_do_not_widen_its_users(estate: Path) -> None:
    """b's tests walk the repo root; a's gate never compiles or runs b's tests."""
    (estate / "crates" / "b" / "tests").mkdir()
    (estate / "crates" / "b" / "tests" / "walk.rs").write_text(
        "#[test]\nfn walks() {\n"
        '    let _ = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");\n}\n'
    )
    _git(estate, "add", "-A")
    assert _groups(estate, "a").returncode == 0
    _edit(estate, "README.md", "more prose\n")
    assert _skipped(_groups(estate, "a"), "crate"), "b's own tests widened a's scope"


def _reads_at_compile_time(estate: Path, target: Path) -> None:
    """crates/c reads `target` through a path built at compile time (no `../` in any source), so
    only the compiler's dep-info can see it."""
    (estate / "crates" / "c" / "build.rs").write_text(
        f'fn main() {{\n    println!("cargo:rustc-env=SHARED={target}");\n}}\n'
    )
    _edit(estate, "crates/c/src/lib.rs", '\npub const S: &str = include_str!(env!("SHARED"));\n')


def test_a_build_that_read_a_file_outside_its_scope_is_keyed_on_it_from_then_on(
    estate: Path,
) -> None:
    """The dep-info NAMES what the build read, so the crate is keyed on it rather than run on every
    push forever: a private server workspace's vpn-watchdog-rs read `compose/` and
    `docker-compose.yml` and was never recorded at all (2026-10-06). The run that learns records
    nothing -- those files were not keyed BEFORE it ran, so a change during it would go unseen; the
    next run keys them before and after, and records. An edit to a learned file re-gates the
    crate."""
    (estate / "shared.txt").write_text("v1\n")
    _reads_at_compile_time(estate, estate / "shared.txt")
    _git(estate, "add", "-A")
    first = _gate(estate, "c", GOH_RUST_GROUPS="crate")
    out = first.stdout + first.stderr
    assert first.returncode == 0, out
    assert "not recorded" in out and "shared.txt" in out and "keyed on" in out, out
    second = _gate(estate, "c", GOH_RUST_GROUPS="crate")
    assert second.returncode == 0 and not _skipped(second, "crate"), second.stdout + second.stderr
    third = _gate(estate, "c", GOH_RUST_GROUPS="crate")
    assert _skipped(third, "crate"), "a learned read was not keyed: the crate never records"
    _edit(estate, "shared.txt", "v2\n")
    fourth = _gate(estate, "c", GOH_RUST_GROUPS="crate")
    assert fourth.returncode == 0 and not _skipped(fourth, "crate"), (
        "an edit to a file the build read did not re-gate the crate"
    )


def test_a_read_outside_the_repository_is_never_learned_or_recorded(
    estate: Path, tmp_path: Path
) -> None:
    """A file outside the repository has no git object to key on: nothing is recorded, ever."""
    outside = tmp_path / "outside.txt"
    outside.write_text("v1\n")
    _reads_at_compile_time(estate, outside)
    for _ in range(2):  # the second run is the one that would record what the first had learned
        r = _groups(estate, "c", CRATE_DOES="cargo check -q --offline --locked")
        out = r.stdout + r.stderr
        assert r.returncode == 0 and not _skipped(r, "crate"), out
        assert "not recorded as proven" in out and str(outside) in out, out


def test_the_push_gates_change_base_is_no_groups_configuration(estate: Path) -> None:
    """GOH_CHANGE_BASE is a new commit on every push and only crates-behind reads it, outside every
    group: while it sat in the environment each group's key was new on every push, so nothing --
    the Rust coverage measurement included -- was reused from one land to the next
    (GOH.RUSTCOVREUSE)."""
    _git(estate, "commit", "-qm", "one push")
    one = _git(estate, "rev-parse", "HEAD").strip()
    _git(estate, "commit", "-q", "--allow-empty", "-m", "the next")
    first = _groups(estate, "c", GOH_CHANGE_BASE=one)
    assert first.returncode == 0, first.stdout + first.stderr
    again = _groups(estate, "c", GOH_CHANGE_BASE=_git(estate, "rev-parse", "HEAD").strip())
    assert again.returncode == 0, again.stdout + again.stderr
    assert _skipped(again, "crate"), again.stdout + again.stderr


def test_a_path_dependency_in_the_gates_checkout_keeps_the_crate_cache_on(estate: Path) -> None:
    """antiknob (R3.13): `goh-tui = { path = "../gates_of_heck/crates/goh-tui" }` lies outside the
    repository, so `goh rust-scope` refused and the per-crate cache was OFF on every push. The gates
    checkout's HEAD, diff and untracked files are already in every proven key, so a dependency
    inside it is keyed: the crate's second run is a hit."""
    gates = Path(os.environ["SCOPED_CACHE_GATES"])
    _crate(
        estate,
        "d",
        deps=f'goh-tui = {{ path = "{gates / "crates" / "goh-tui"}" }}\n',
        lib="pub fn t() -> usize {\n    goh_tui::OK.len()\n}\n",
    )
    subprocess.run(
        ["cargo", "generate-lockfile", "--offline"],
        cwd=estate / "crates" / "d",
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    _git(estate, "add", "-A")
    first = _groups(estate, "d", CRATE_DOES="cargo check -q --offline --locked")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "per-crate proven cache off" not in first.stdout + first.stderr, (
        first.stdout + first.stderr
    )
    again = _groups(estate, "d", CRATE_DOES="cargo check -q --offline --locked")
    assert _skipped(again, "crate"), again.stdout + again.stderr


@pytest.mark.parametrize("listed", [False, True])
def test_a_group_that_rewrites_a_kept_path_is_named(estate: Path, listed: bool) -> None:
    """GOH.KEEP1 in rust_gate's groups: the ratchet names the group by its label, not its step
    string (which carries a config hash). Unlisted is red; listed is a warning; neither records."""
    (estate / "build").mkdir()
    (estate / "build" / "out.bin").write_text("before\n")
    (estate / ".gitignore").write_text("target/\n.build/\nbuild/\n")
    write_gatesrc(estate, "GOH_DEPS_OFFLINE=1\nGOH_EXPORT_KEEP='build/out.bin'\n")
    if listed:
        (estate / "kept_rewrite_allow.json").write_text(
            '{"entries": [{"step": "rust_gate crate crates/c", "path": "build/out.bin",'
            ' "reason": "the fixture clippy rebuilds it"}]}'
        )
    _git(estate, "add", "-A")
    rewrites = f"echo rebuilt >>{estate}/build/out.bin"
    r = _groups(estate, "c", CRATE_DOES=rewrites)
    out = r.stdout + r.stderr
    assert "step 'rust_gate crate crates/c' rewrote kept path 'build/out.bin'" in out, out
    assert (r.returncode == 0) == listed, out
    assert not _skipped(_groups(estate, "c"), "crate"), "a group that rewrote a kept path recorded"
