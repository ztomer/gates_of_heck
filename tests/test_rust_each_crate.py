"""rust_gate.sh --each-crate: every crate of a repo through ONE scheduler, repo-wide scans once.

media_server hand-rolled this (`xargs -P 4`, a log dir, a `*.failed` sweep), and its 29 crates,
started together, all missed the shared repo-scan record at once and ran the whole-repo scans 29
times (BACKLOG P1f). On real cargo crates:
* every top-level crate is gated, a nested manifest (a testkit) is its crate's business;
* the repo-wide scans run ONCE; biggest crate first;
* a red crate is named and the others still finish; bad config is refused, named;
* GOH_EXCLUDE is a regex over repo-relative FILE paths here as everywhere else (the manifest's);
* `--list` prints the crates a run gates, through the run's own selection, and runs nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_rust_gate_scoped_cache import _crate, _git, _native, gates  # noqa: F401  # fixtures
from conftest import hermetic_env

pytestmark = [
    pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed"),
    pytest.mark.xdist_group("rust_scoped_cache"),
]


@pytest.fixture
def estate(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _crate(repo, "b")
    _crate(repo, "a", deps='b = { path = "../b" }\n', lib="pub fn g() -> u64 {\n    b::f()\n}\n")
    _crate(repo, "c")
    for n in range(3):  # c is the biggest crate by tracked .rs files
        (repo / "crates" / "c" / "src" / f"m{n}.rs").write_text("")
    (repo / ".gatesrc").write_text("GOH_DEPS_OFFLINE=1\nGOH_PROVEN=0\n")
    (repo / ".gitignore").write_text("target/\n")
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


def _each(
    repo: Path, timings: Path | None = None, *flags: str, **env: str
) -> subprocess.CompletedProcess:
    full = hermetic_env(drop_git=True)
    g = Path(os.environ["SCOPED_CACHE_GATES"])
    full.update(GOH_DIR=str(g), GOH_BIN=os.environ["SCOPED_CACHE_GOH"], **env)
    if timings is not None:
        full["GOH_TIMINGS"] = str(timings)
    return subprocess.run(
        ["bash", str(g / "gates" / "rust_gate.sh"), "--each-crate", *flags, str(repo)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=full,
        timeout=600,
        check=False,
    )


def test_every_crate_gated_repo_scans_once_biggest_first(estate: Path, tmp_path: Path) -> None:
    timings = tmp_path / "t.jsonl"
    r = _each(estate, timings)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "rust gate over 3 crate(s)" in out, out
    labels = [json.loads(line)["label"] for line in timings.read_text().splitlines()]
    assert labels.count("lint policy is inherited") == 1, labels
    assert labels.count("fmt") == 3, labels
    crate_steps = [
        line
        for line in r.stdout.splitlines()
        if "GOH_RUST_GROUPS=crate,coverage" in line and "→" in line
    ]
    assert crate_steps and crate_steps[0].rstrip().endswith("crates/c"), crate_steps


def test_a_red_crate_is_named_and_the_others_finish(estate: Path) -> None:
    (estate / "crates" / "b" / "src" / "lib.rs").write_text("pub fn f()->u64{1}\n")  # fmt-red
    _git(estate, "add", "-A")
    r = _each(estate)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "step(s) failed" in r.stderr and "crates/b" in r.stderr.split("step(s) failed", 1)[1], (
        r.stderr
    )
    assert r.stdout.count("✓ [") >= 2, "the other crates did not finish"


def test_a_nested_manifest_is_its_crates_business(estate: Path) -> None:
    kit = estate / "crates" / "c" / "testkit"
    (kit / "src").mkdir(parents=True)
    (kit / "Cargo.toml").write_text(
        '[package]\nname = "kit"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    (kit / "src" / "lib.rs").write_text("")
    _git(estate, "add", "-A")
    r = _each(estate, GOH_CI_STEPS="unused")
    assert "rust gate over 3 crate(s)" in r.stdout + r.stderr


@pytest.mark.parametrize(("key", "value"), [("GOH_RUST_JOBS", "many"), ("GOH_RUST_JOBS", "0")])
def test_a_bad_job_count_is_refused(estate: Path, key: str, value: str) -> None:
    (estate / ".gatesrc").write_text(f"{key}={value}\n")
    r = _each(estate)
    assert r.returncode != 0 and key in r.stderr and value in r.stderr, r.stderr


def test_an_unknown_group_is_a_miswiring(estate: Path) -> None:
    g = Path(os.environ["SCOPED_CACHE_GATES"])
    env = hermetic_env(drop_git=True)
    env.update(GOH_DIR=str(g), GOH_RUST_GROUPS="crate,lint")
    r = subprocess.run(
        ["bash", str(g / "gates" / "rust_gate.sh"), str(estate), str(estate / "crates" / "c")],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert r.returncode != 0 and "GOH_RUST_GROUPS='crate,lint'" in r.stderr, r.stderr


def _gated(r: subprocess.CompletedProcess) -> list[str]:
    """The crates a run gated, in its declared order: each per-crate step's last word."""
    return [
        line.split()[-1]
        for line in r.stdout.splitlines()
        if "GOH_RUST_GROUPS=crate,coverage" in line and "→" in line
    ]


def test_a_trailing_slash_directory_pattern_excludes_its_crate(estate: Path) -> None:
    # Everywhere else GOH_EXCLUDE is a regex over repo-relative FILE paths, so a directory is
    # written `^crates/c/`. Matched against the bare directory `crates/c`, it matched nothing and
    # the crate it names was gated anyway. Read through --list: the test below holds the list equal
    # to what a run gates, so the selection is checked here without running a whole gate.
    (estate / ".gatesrc").write_text("GOH_EXCLUDE='^crates/c/'\n")
    _git(estate, "add", "-A")
    r = _each(estate, None, "--list")
    assert (r.returncode, r.stdout) == (0, "crates/a\ncrates/b\n"), r.stdout + r.stderr


def test_exclude_reads_the_python_dialect_every_other_check_reads(estate: Path) -> None:
    # `^(?!crates/c/)` scopes a scan TO a directory (ztools); `grep -E` has no look-ahead, refused
    # it, and `|| true` turned the refusal into "no tracked Cargo.toml".
    (estate / ".gatesrc").write_text("GOH_EXCLUDE='^(?!crates/c/)'\n")
    _git(estate, "add", "-A")
    r = _each(estate, None, "--list")
    assert (r.returncode, r.stdout) == (0, "crates/c\n"), r.stdout + r.stderr


def test_a_bad_exclude_is_refused_named(estate: Path) -> None:
    (estate / ".gatesrc").write_text("GOH_EXCLUDE='('\n")
    _git(estate, "add", "-A")
    r = _each(estate, None, "--list")
    assert r.returncode != 0 and "GOH_EXCLUDE" in r.stderr and "'('" in r.stderr, r.stderr


def test_list_prints_exactly_the_crates_a_run_gates_in_order(estate: Path) -> None:
    kit = estate / "crates" / "c" / "testkit"  # nested: c's business, never its own crate
    (kit / "src").mkdir(parents=True)
    (kit / "Cargo.toml").write_text(
        '[package]\nname = "kit"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    (kit / "src" / "lib.rs").write_text("")
    _git(estate, "add", "-A")
    _crate(estate, "d")  # untracked: not in the index, so not a crate of the repo
    listed = _each(estate, None, "--list")
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert listed.stdout.splitlines() == ["crates/c", "crates/a", "crates/b"], listed.stdout
    run = _each(estate)
    assert _gated(run) == listed.stdout.splitlines(), run.stdout + run.stderr


def test_list_nested_under_an_excluded_crate_stays_its_business(estate: Path) -> None:
    # The header's order: top-level crates first, THEN minus GOH_EXCLUDE. Excluding first promoted
    # an excluded crate's testkit to a crate of its own.
    kit = estate / "crates" / "c" / "testkit"
    (kit / "src").mkdir(parents=True)
    (kit / "Cargo.toml").write_text('[package]\nname = "kit"\nversion = "0.1.0"\n')
    (estate / ".gatesrc").write_text("GOH_EXCLUDE='^crates/c/Cargo\\.toml$'\n")
    _git(estate, "add", "-A")
    r = _each(estate, None, "--list")
    assert (r.returncode, r.stdout) == (0, "crates/a\ncrates/b\n"), r.stdout + r.stderr


def test_list_runs_nothing(estate: Path, tmp_path: Path) -> None:
    shim = tmp_path / "shim"
    shim.mkdir()
    ran = tmp_path / "cargo-ran"
    (shim / "cargo").write_text(f"#!/bin/sh\ntouch {ran}\nexit 1\n")
    (shim / "cargo").chmod(0o755)
    timings = tmp_path / "t.jsonl"
    r = _each(estate, timings, "--list", PATH=f"{shim}:{os.environ['PATH']}")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout == "crates/c\ncrates/a\ncrates/b\n", r.stdout
    assert not ran.exists(), "cargo ran"
    assert not timings.exists() or not timings.read_text().strip(), timings.read_text()
    assert not any(estate.glob("crates/*/target")), "a build ran"
