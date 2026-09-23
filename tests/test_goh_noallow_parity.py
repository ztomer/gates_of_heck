"""No-allow parity: `goh no-allow` agrees with `check_no_allow.py`.

Fixture repos per case; asserts identical exit codes plus identical
stdout/stderr. A suppression shape, scope rule, message, or stream
drifting on either side goes red.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "checks" / "check_no_allow.py"

CARGO = b'[package]\nname = "x"\nversion = "0.1.0"\nedition = "2021"\n'


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def make_repo(tmp_path: Path, files: dict[str, bytes]) -> Path:
    repo = tmp_path
    repo.mkdir(exist_ok=True)
    _git(repo, "init", "-q")
    for name, content in files.items():
        dest = repo / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    _git(repo, "add", "-A")
    return repo


def run_py(repo: Path, *args: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["python3", str(CHECK), *args], cwd=repo, capture_output=True, text=True
    )
    return r.returncode, r.stdout, r.stderr


def run_goh(goh: Path, repo: Path, *args: str) -> tuple[int, str, str]:
    import os

    env = dict(os.environ, GOH_DIR=str(ROOT))
    r = subprocess.run(
        [str(goh), "no-allow", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )
    return r.returncode, r.stdout, r.stderr


def rs(body: str) -> bytes:
    return body.encode()


CASES: dict[str, dict[str, bytes]] = {
    "clean": {"Cargo.toml": CARGO, "src/a.rs": rs("fn f() {}\n")},
    "literal_outer": {"Cargo.toml": CARGO, "src/a.rs": rs("#[allow(dead_code)]\nfn f() {}\n")},
    "literal_inner": {"Cargo.toml": CARGO, "src/a.rs": rs("#![allow(clippy::all)]\nfn f() {}\n")},
    "literal_expect": {"Cargo.toml": CARGO, "src/a.rs": rs("#[expect(dead_code)]\nfn f() {}\n")},
    "doc_mention": {
        "Cargo.toml": CARGO,
        "src/a.rs": rs("/// Mentioning #[allow(dead_code)] is prose.\nfn f() {}\n"),
    },
    "block_mention": {
        "Cargo.toml": CARGO,
        "src/a.rs": rs("/* #[expect(x)] */\nfn f() {}\n"),
    },
    "attr_after_block_close": {
        "Cargo.toml": CARGO,
        "src/a.rs": rs("/* note */ #[allow(x)]\nfn f() {}\n"),
    },
    "nested_block": {
        "Cargo.toml": CARGO,
        "src/a.rs": rs("/* outer /* inner */ #[allow(x)]\nmore\n*/\n#[allow(y)]\nfn f() {}\n"),
    },
    "cfg_attr_single": {
        "Cargo.toml": CARGO,
        "src/a.rs": rs('#[cfg_attr(target_os = "macos", expect(unsafe_code))]\nfn f() {}\n'),
    },
    "cfg_attr_multiline": {
        "Cargo.toml": CARGO,
        "src/a.rs": rs('#[cfg_attr(\n    target_os = "macos",\n    allow(dead_code),\n)]\nfn f() {}\n'),
    },
    "cfg_attr_clean": {
        "Cargo.toml": CARGO,
        "src/a.rs": rs('#[cfg_attr(target_os = "macos", must_use)]\nfn f() {}\n'),
    },
    "cfg_attr_path": {
        "Cargo.toml": CARGO,
        "src/a.rs": rs("#[cfg_attr(x, cfg::allow(y))]\nfn f() {}\n"),
    },
    "string_wrapped": {
        "Cargo.toml": CARGO,
        "src/a.rs": rs('#[cfg_attr(x, "allow(y)")]\nfn f() {}\n'),
    },
    "string_literal": {
        "Cargo.toml": CARGO,
        "src/a.rs": rs('fn f() {\n    let s = "#[allow(x)]";\n}\n'),
    },
    "generated": {
        "Cargo.toml": CARGO,
        "src/a.rs": rs("// @generated\n#[allow(x)]\nfn f() {}\n"),
    },
    "tests_scope": {"Cargo.toml": CARGO, "tests/f.rs": rs("#[allow(x)]\nfn f() {}\n")},
    "nested_crate": {
        "Cargo.toml": CARGO,
        "crates/x/Cargo.toml": CARGO,
        "crates/x/src/m.rs": rs("#[allow(x)]\nfn f() {}\n"),
    },
    "build_rs": {"Cargo.toml": CARGO, "build.rs": rs("#[allow(x)]\nfn main() {}\n")},
    "outside_scope": {"Cargo.toml": CARGO, "tools/a.rs": rs("#[allow(x)]\nfn f() {}\n")},
    "blind_layout": {"Cargo.toml": CARGO, "other/a.rs": rs("fn f() {}\n")},
    "no_rust": {"README.md": b"no rust here\n"},
}


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("staged", [False, True])
def test_no_allow_agrees(goh: Path, tmp_path: Path, name: str, staged: bool) -> None:
    repo = make_repo(tmp_path, CASES[name])
    args = ["--staged"] if staged else []
    assert run_goh(goh, repo, *args) == run_py(repo, *args), name


def test_exclude_skips_vendored_crate(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        {
            "Cargo.toml": CARGO,
            "vendor/x/src/a.rs": rs("#[allow(x)]\nfn f() {}\n"),
            "src/b.rs": rs("fn f() {}\n"),
        },
    )
    args = ["--exclude", "vendor/"]
    got = run_goh(goh, repo, *args)
    assert got == run_py(repo, *args), got
    assert got[0] == 0


def test_staged_indexes_the_violation(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"Cargo.toml": CARGO, "src/a.rs": rs("fn f() {}\n")})
    (repo / "src" / "a.rs").write_bytes(rs("#[allow(x)]\nfn f() {}\n"))
    _git(repo, "add", "src/a.rs")
    (repo / "src" / "a.rs").write_bytes(rs("fn f() {}\n"))
    got = run_goh(goh, repo, "--staged")
    assert got == run_py(repo, "--staged"), got
    assert got[0] == 1
    assert "src/a.rs:1" in got[1]
