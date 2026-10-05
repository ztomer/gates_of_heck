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

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUST_GATE = ROOT / "gates" / "rust_gate.sh"
GOH = ROOT / "target" / "release" / "goh"

pytestmark = [
    pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed"),
    pytest.mark.xdist_group("rust_scoped_cache"),
]

LIB = "pub fn f() -> u64 {\n    1\n}\n"


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout


def _crate(repo: Path, name: str, deps: str = "", lib: str = LIB) -> None:
    d = repo / "crates" / name
    (d / "src").mkdir(parents=True)
    (d / "Cargo.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n{deps}'
    )
    (d / "src" / "lib.rs").write_text(lib)


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
        )
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    return repo


def _gate(repo: Path, crate: str, **env: str) -> subprocess.CompletedProcess:
    full = {k: v for k, v in os.environ.items() if not k.startswith(("GOH_", "GIT_"))}
    full.update(GOH_DIR=str(ROOT), GOH_BIN=str(GOH), **env)
    return subprocess.run(
        ["bash", str(RUST_GATE), str(repo), str(repo / "crates" / crate)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=full,
        timeout=600,
    )


def _skipped(r: subprocess.CompletedProcess, group: str) -> bool:
    return f"{group} checks: proven on these inputs" in r.stdout + r.stderr


def _edit(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.write_text(path.read_text() + text)
    _git(repo, "add", "-A")


def test_only_the_edited_crate_and_its_users_are_re_gated(estate: Path) -> None:
    for name in ("a", "b", "c"):
        first = _gate(estate, name)
        assert first.returncode == 0, first.stdout + first.stderr
        assert not _skipped(first, "crate"), name
    again = _gate(estate, "c")
    assert _skipped(again, "crate") and _skipped(again, "repo"), again.stdout + again.stderr

    _edit(estate, "crates/b/src/lib.rs", "pub fn h() -> u64 {\n    2\n}\n")
    after = {name: _gate(estate, name) for name in ("a", "b", "c")}
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
    assert _gate(estate, "c").returncode == 0
    _edit(estate, "README.md", "more prose\n")
    readme = _gate(estate, "c")
    assert _skipped(readme, "crate"), readme.stdout + readme.stderr
    assert not _skipped(readme, "repo"), "the repo-wide scans are keyed on the whole tree"
    (estate / "clippy.toml").write_text("too-many-arguments-threshold = 9\n")
    _git(estate, "add", "-A")
    config = _gate(estate, "c")
    assert not _skipped(config, "crate"), "a new clippy.toml above the crate did not re-gate it"


def test_a_runtime_reach_outside_the_crate_keys_it_on_the_whole_tree(estate: Path) -> None:
    _edit(estate, "crates/c/src/lib.rs", '\npub const FIXTURE: &str = "../shared/fixture.json";\n')
    assert _gate(estate, "c").returncode == 0
    _edit(estate, "README.md", "more prose\n")
    again = _gate(estate, "c")
    assert not _skipped(again, "crate"), "a crate that reads ../ was keyed on its own dir only"


def test_a_build_that_read_outside_the_scope_is_never_recorded(estate: Path) -> None:
    """No `../` anywhere in the source: the path is built at compile time, so only the compiler's
    dep-info can see that the crate read `shared.txt`."""
    (estate / "shared.txt").write_text("v1\n")
    build = estate / "crates" / "c" / "build.rs"
    build.write_text(
        "fn main() {\n"
        '    let dir = std::path::PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap_or_default());\n'
        "    let root = dir\n"
        "        .parent()\n"
        "        .and_then(std::path::Path::parent)\n"
        '        .map(|p| p.join("shared.txt"));\n'
        "    println!(\n"
        '        "cargo:rustc-env=SHARED={}",\n'
        "        root.unwrap_or_default().display()\n"
        "    );\n"
        "}\n"
    )
    _edit(estate, "crates/c/src/lib.rs", '\npub const S: &str = include_str!(env!("SHARED"));\n')
    first = _gate(estate, "c")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "not recorded as proven" in first.stdout + first.stderr and "shared.txt" in (
        first.stdout + first.stderr
    ), first.stdout + first.stderr
    again = _gate(estate, "c")
    assert not _skipped(again, "crate"), "a record was written for a build that read outside"


def test_goh_proven_0_runs_everything(estate: Path) -> None:
    assert _gate(estate, "c").returncode == 0
    off = _gate(estate, "c", GOH_PROVEN="0")
    assert not _skipped(off, "crate") and not _skipped(off, "repo"), off.stdout
