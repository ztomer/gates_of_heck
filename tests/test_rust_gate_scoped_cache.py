"""rust_gate.sh proves each group once on ITS OWN inputs (BACKLOG P3), and the record cannot lie.

The proven cache keyed every step on the whole tree: one README line re-gated all 29 of
media_server's crates. Now the crate group is keyed on the crate, the packages it reaches by
`path =`, and the config files it reads; the repo-wide scans on the whole tree. Each way a hit
could be wrong is planted here on REAL cargo crates:

* an edit to a crate re-gates it and nothing else; an edit to a path dependency re-gates its users;
* an edit outside every crate (a README) re-gates nothing; an edit to shared config re-gates all;
* a source reaching outside its crate at run time widens that crate's scope to the whole tree;
* a build that READ a file outside the scope (dep-info) is never recorded;
* GOH_PROVEN=0 runs everything.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import _fake_cargo
import pytest
from _fast_git import fast_init
from conftest import hermetic_env

ROOT = Path(__file__).resolve().parent.parent

# NOT an xdist group: every test builds its own crates in its own temp repo, records proofs in that
# repo's own git dir and runs the session's binary, so nothing here is shared or mutable. Grouped,
# its ~55 s of cargo work ran on ONE worker and was the whole suite's critical path (P5, measured
# 2026-10-06 with --durations).
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
def gates(tmp_path_factory) -> Path:
    """A FROZEN clean clone of today's gates. The proven key includes the gates checkout's identity
    (its diff and untracked files), and other tests of a parallel suite briefly create files in
    the live one -- which the cache rightly refuses to trust, and which made this suite flaky."""
    from test_gate_environment import _clean_checkout_of_todays_gates

    return _clean_checkout_of_todays_gates(tmp_path_factory.mktemp("gates"))


@pytest.fixture(autouse=True)
def _native(goh: Path, gates: Path, monkeypatch) -> None:
    """The session's own goh build (conftest), never cargo's target path: another test may be
    re-linking that while this one reads it -- the race conftest's fixture exists to close."""
    monkeypatch.setenv("SCOPED_CACHE_GOH", str(goh))
    monkeypatch.setenv("SCOPED_CACHE_GATES", str(gates))


@pytest.fixture
def estate(tmp_path: Path) -> Path:
    """crates/a uses crates/b by path; crates/c stands alone. Each is its own workspace."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _crate(repo, "b")
    _crate(repo, "a", deps='b = { path = "../b" }\n', lib="pub fn g() -> u64 {\n    b::f()\n}\n")
    _crate(repo, "c")
    (repo / "README.md").write_text("# estate\n")
    (repo / ".gatesrc").write_text("GOH_DEPS_OFFLINE=1\n")
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
    return repo


@pytest.fixture
def fake_cargo(tmp_path: Path, monkeypatch) -> Path:
    """The recorded fake (tests/_fake_cargo.py) for a test of the cache's SCOPING: what it keys and
    re-gates is decided by cargo's metadata, never by a compile. Returns the call log."""
    bin_dir, log = _fake_cargo.install(tmp_path / "fake-cargo")
    monkeypatch.setenv("SCOPED_CACHE_FAKE_CARGO", str(bin_dir))
    return log


def _gate(repo: Path, crate: str, **env: str) -> subprocess.CompletedProcess:
    full = hermetic_env(drop_git=True)
    gates = Path(os.environ["SCOPED_CACHE_GATES"])
    full.update(GOH_DIR=str(gates), GOH_BIN=os.environ["SCOPED_CACHE_GOH"], **env)
    if fake := os.environ.get("SCOPED_CACHE_FAKE_CARGO"):
        full["PATH"] = f"{fake}:{full['PATH']}"
    return subprocess.run(
        ["bash", str(gates / "gates" / "rust_gate.sh"), str(repo), str(repo / "crates" / crate)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=full,
        timeout=600,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _skipped(r: subprocess.CompletedProcess, group: str) -> bool:
    return f"{group} checks: proven on these inputs" in r.stdout + r.stderr


def _edit(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.write_text(path.read_text() + text)
    _git(repo, "add", "-A")


def test_only_the_edited_crate_and_its_users_are_re_gated(estate: Path, fake_cargo: Path) -> None:
    for name in ("a", "b", "c"):
        first = _gate(estate, name)
        assert first.returncode == 0, first.stdout + first.stderr
        assert not _skipped(first, "crate"), name
        # The repo-wide scans read the whole repo whichever crate called them: the first crate
        # records them and every later crate of the same tree finds the record.
        assert _skipped(first, "repo") == (name != "a"), (name, first.stdout + first.stderr)
    again = _gate(estate, "c")
    assert _skipped(again, "crate") and _skipped(again, "repo"), again.stdout + again.stderr

    _edit(estate, "crates/b/src/lib.rs", "pub fn h() -> u64 {\n    2\n}\n")
    after = {name: _gate(estate, name) for name in ("a", "b", "c")}
    assert all(r.returncode == 0 for r in after.values()), {
        n: r.stdout + r.stderr for n, r in after.items() if r.returncode
    }
    # The record says what RAN, not only what the gate printed: clippy, once per gated crate.
    linted = [Path(d).name for d in _fake_cargo.calls(fake_cargo, "clippy")]
    assert linted == ["a", "b", "c", "a", "b"], linted
    assert not _skipped(after["b"], "crate"), "the edited crate was not re-gated"
    assert not _skipped(after["a"], "crate"), (
        "a user of the edited path dependency was not re-gated"
    )
    assert _skipped(after["c"], "crate"), "an unrelated crate was re-gated"


def test_an_edit_outside_every_crate_re_gates_no_crate_but_shared_config_re_gates_all(
    estate: Path, fake_cargo: Path
) -> None:
    assert _gate(estate, "c").returncode == 0
    _edit(estate, "README.md", "more prose\n")
    readme = _gate(estate, "c")
    assert _skipped(readme, "crate"), readme.stdout + readme.stderr
    assert not _skipped(readme, "repo"), "the repo-wide scans are keyed on the whole tree"
    (estate / "clippy.toml").write_text("too-many-arguments-threshold = 9\n")
    _git(estate, "add", "-A")
    config = _gate(estate, "c")
    assert not _skipped(config, "crate"), "a new clippy.toml above the crate did not re-gate it"


def test_a_resolvable_reach_outside_the_crate_keys_on_exactly_that_file(
    estate: Path, fake_cargo: Path
) -> None:
    """A test reading `../shared/fixture.json` (relative to its package) is keyed on that file:
    editing it re-gates the crate, a README edit does not. Measured 2026-10-05: every media_server
    crate has include_str!("../../../VERSION"), and keying them on the whole tree for it made the
    cache worthless."""
    (estate / "crates" / "shared").mkdir()
    (estate / "crates" / "shared" / "fixture.json").write_text("{}\n")
    _edit(estate, "crates/c/src/lib.rs", '\npub const FIXTURE: &str = "../shared/fixture.json";\n')
    assert _gate(estate, "c").returncode == 0
    _edit(estate, "README.md", "more prose\n")
    assert _skipped(_gate(estate, "c"), "crate"), "a README edit re-gated a precisely keyed crate"
    _edit(estate, "crates/shared/fixture.json", "\n")
    assert not _skipped(_gate(estate, "c"), "crate"), "editing the file it reads did not re-gate"


def test_an_unnameable_reach_keys_the_crate_on_the_whole_tree(
    estate: Path, fake_cargo: Path
) -> None:
    _edit(
        estate,
        "crates/c/src/lib.rs",
        "\npub fn up() -> std::path::PathBuf {\n"
        '    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..")\n'
        "}\n",
    )
    assert _gate(estate, "c").returncode == 0
    _edit(estate, "README.md", "more prose\n")
    assert not _skipped(_gate(estate, "c"), "crate"), "a `..` join was keyed on the crate only"


def test_a_path_dependencys_own_test_reads_do_not_widen_its_users(estate: Path) -> None:
    """b's tests walk the repo root; a's gate never compiles or runs b's tests."""
    (estate / "crates" / "b" / "tests").mkdir()
    (estate / "crates" / "b" / "tests" / "walk.rs").write_text(
        "#[test]\nfn walks() {\n"
        '    let _ = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");\n}\n'
    )
    _git(estate, "add", "-A")
    assert _gate(estate, "a").returncode == 0
    _edit(estate, "README.md", "more prose\n")
    assert _skipped(_gate(estate, "a"), "crate"), "b's own tests widened a's scope"


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
    push forever: media_server's vpn-watchdog-rs read `compose/` and `docker-compose.yml` and was
    never recorded at all (2026-10-06). The run that learns records nothing -- those files were
    not keyed BEFORE it ran, so a change during it would go unseen; the next run keys them before
    and after, and records. An edit to a learned file re-gates the crate."""
    (estate / "shared.txt").write_text("v1\n")
    _reads_at_compile_time(estate, estate / "shared.txt")
    _git(estate, "add", "-A")
    first = _gate(estate, "c")
    out = first.stdout + first.stderr
    assert first.returncode == 0, out
    assert "not recorded" in out and "shared.txt" in out and "keyed on" in out, out
    second = _gate(estate, "c")
    assert second.returncode == 0 and not _skipped(second, "crate"), second.stdout + second.stderr
    third = _gate(estate, "c")
    assert _skipped(third, "crate"), "a learned read was not keyed: the crate never records"
    _edit(estate, "shared.txt", "v2\n")
    fourth = _gate(estate, "c")
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
    first = _gate(estate, "c")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "not recorded" in first.stdout + first.stderr, first.stdout + first.stderr
    for _ in range(2):
        again = _gate(estate, "c")
        assert not _skipped(again, "crate"), "a record was written for a build that read outside"


def test_goh_proven_0_runs_everything(estate: Path, fake_cargo: Path) -> None:
    assert _gate(estate, "c").returncode == 0
    off = _gate(estate, "c", GOH_PROVEN="0")
    assert not _skipped(off, "crate") and not _skipped(off, "repo"), off.stdout


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
    first = _gate(estate, "d")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "per-crate proven cache off" not in first.stdout + first.stderr, (
        first.stdout + first.stderr
    )
    again = _gate(estate, "d")
    assert _skipped(again, "crate"), again.stdout + again.stderr
