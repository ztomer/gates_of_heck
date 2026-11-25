"""A land's Rust steps run when the Rust they judge changed, and only then (GOH.GATEFLOOR).

`cargo test (plain)` (41-69 s) sat in the tree-keyed coverage group, and goh's crate opens
CARGO_MANIFEST_DIR/../.. in its tests, so `goh rust-scope` keyed the crate group -- both clippy
runs, 10-18 s each -- on the whole tree too: GOH.DURATIONSPRUNE's land, tests/ alone, ran all
three. Now (gates/_rust_inputs.sh) the crate group is keyed on what the COMPILER reads and cargo
test on the tree minus GOH_RUST_TEST_UNREAD, each beside the gates that run it -- rust_gate.sh's
closure, not the whole goh content, which a goh land's head export of main moves on every Python
change.

Planted on a crate that reaches `..` at run time, as goh's does, with the recorded fake cargo:
* a change to the Python suite alone skips clippy and cargo test;
* a change to other Python the tests may run (lib/) skips clippy, runs cargo test;
* a change to the gates outside rust_gate.sh's closure skips both;
* a Rust change runs both.
And goh's own GOH_RUST_TEST_UNREAD holds: no Rust source of goh names a path it excludes.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import _fake_cargo
import pytest
from _fast_git import fast_init
from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

TREE_READS = ("crates/", ".gatesrc")  # the unread guard reads every Rust source
pytestmark = [pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")]

# A runtime reach no scope can name: `goh rust-scope` keys the crate's tests on the whole tree.
LIB = (
    "pub fn up() -> std::path::PathBuf {\n"
    '    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..")\n'
    "}\n"
)


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(repo),
                           *args], check=True, capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL).stdout  # fmt: skip


@pytest.fixture
def gates(tmp_path: Path) -> Path:
    """Today's gates, uncommitted work included, in a checkout of this test's own (it edits one)."""
    from test_gate_environment import _clean_checkout_of_todays_gates

    return _clean_checkout_of_todays_gates(tmp_path / "gates", rust=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    d = r / "crates" / "c"
    (d / "src").mkdir(parents=True)
    (d / "Cargo.toml").write_text(
        '[package]\nname = "c"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n'
    )
    (d / "src" / "lib.rs").write_text(LIB)
    (r / "tests").mkdir()
    (r / "tests" / "test_x.py").write_text("def test_x():\n    pass\n")
    (r / "lib").mkdir()
    (r / "lib" / "tool.py").write_text("X = 1\n")
    (r / ".gitignore").write_text("target/\n")
    write_gatesrc(
        r,
        "GOH_DEPS_OFFLINE=1\nGOH_COV_FLOOR_RUST=50\nGOH_RUST_TEST_UNREAD='tests/**/*.py'\n",
    )
    subprocess.run(["cargo", "generate-lockfile", "--offline"], cwd=d, check=True,
                   capture_output=True, stdin=subprocess.DEVNULL)  # fmt: skip
    fast_init(r)
    _git(r, "add", "-A")
    return r


def _gate(repo: Path, gates: Path, goh: Path, fake: Path) -> str:
    env = hermetic_env(drop_git=True, GOH_DIR=str(gates), PYTHONPATH=str(gates))
    env.update(GOH_BIN=str(goh), GOH_RUST_COVERAGE="plain")
    env["PATH"] = f"{fake}:{env['PATH']}"
    argv = ["bash", str(gates / "gates" / "rust_gate.sh"), str(repo), str(repo / "crates" / "c")]
    r = subprocess.run(argv, cwd=repo, capture_output=True, text=True, env=env, check=False,
                       timeout=patience(600), stdin=subprocess.DEVNULL)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout + r.stderr


def _ran(log: Path, before: int) -> tuple[int, int]:
    """(clippy runs, cargo test runs) the fake recorded after the first `before` lines."""
    lines = log.read_text().splitlines()[before:] if log.exists() else []
    words = [line.split("\t", 1)[1].split()[0] for line in lines]
    return words.count("clippy"), words.count("test")


def _edit(repo: Path, rel: str) -> None:
    more = "pub fn more() {}\n" if rel.endswith(".rs") else "# edited\n"
    (repo / rel).write_text((repo / rel).read_text() + more)
    _git(repo, "add", "-A")


def _lines(log: Path) -> int:
    return len(log.read_text().splitlines()) if log.exists() else 0


def test_each_rust_step_runs_when_what_it_judges_changed(
    repo: Path, gates: Path, goh: Path, tmp_path: Path
) -> None:
    bin_dir, log = _fake_cargo.install(tmp_path / "fake-cargo")
    first = _gate(repo, gates, goh, bin_dir)
    assert _ran(log, 0) == (1, 1), first
    for rel, want in (
        ("tests/test_x.py", (0, 0)),  # the Python suite: no Rust test reads it
        ("lib/tool.py", (0, 1)),  # Python a Rust test may run: cargo test, never clippy
        ("crates/c/src/lib.rs", (1, 1)),  # Rust: both
    ):
        n = _lines(log)
        _edit(repo, rel)
        out = _gate(repo, gates, goh, bin_dir)
        assert _ran(log, n) == want, (rel, out)
    n = _lines(log)
    # A gates change outside rust_gate.sh's closure moves neither key.
    (gates / "lib" / "zz_unrelated.py").write_text("Z = 1\n")
    _git(gates, "add", "-A")
    _git(gates, "commit", "-qm", "an unrelated gates change")
    out = _gate(repo, gates, goh, bin_dir)
    assert _ran(log, n) == (0, 0), out
    assert "crate checks: proven" in out and "cargo-test checks: proven" in out, out


def _unread() -> list[str]:
    text = (REPO_ROOT / ".gatesrc").read_text()
    m = re.search(r"^GOH_RUST_TEST_UNREAD=(.*)$", text, re.MULTILINE)
    assert m, "goh's .gatesrc sets GOH_RUST_TEST_UNREAD"
    return shlex.split(m.group(1))[0].split()


def test_no_rust_source_of_goh_reads_a_path_it_declares_unread() -> None:
    """The exclusion is a claim about every Rust test, held here by its source: a line that reads
    from the checkout (an include!, a path off CARGO_MANIFEST_DIR or the goh root, a `../` literal)
    naming an excluded path that exists, or joining a wholly excluded directory, is red. A fixture's
    own `docs/map.md`, written in a temp repo, reads nothing of the checkout."""
    globs = _unread()
    tracked = _git(REPO_ROOT, "ls-files").split()
    unread = {p for p in tracked if any(fnmatch.fnmatch(p, g.replace("**/", "*")) or
                                        fnmatch.fnmatch(p, g) for g in globs)}  # fmt: skip
    assert unread, globs
    dirs = {g[: -len("/**")] for g in globs if g.endswith("/**")}
    reads = re.compile(r"include(_str|_bytes)?!|CARGO_MANIFEST_DIR|goh_root|repo_root")
    found = []
    for rs in (p for p in tracked if p.startswith("crates/") and p.endswith(".rs")):
        for n, line in enumerate((REPO_ROOT / rs).read_text().splitlines(), 1):
            for lit in re.findall(r'"((?:[^"\\]|\\.)*)"', line):
                tail = re.sub(r"^(\.\.?/)+", "", lit)
                if tail in unread and (reads.search(line) or lit.startswith("../")):
                    found.append(f"{rs}:{n}: {lit}")
                elif lit in dirs and ".join(" in line and reads.search(line):
                    found.append(f"{rs}:{n}: joins {lit}/")
    assert found == [], found
