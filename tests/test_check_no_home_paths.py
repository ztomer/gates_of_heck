"""checks/check_no_home_paths.py — no hard-coded home paths in a tracked tree.

The class behind the salary CLI outage (2026-09): a location under someone's
HOME written into a script, a shell function, a binary or a doc. The gate
must go RED on each of the four shapes, honour `path-ok: <reason>` and refuse
a bare marker, honour --exclude, and refuse to report clean over zero files.
"""

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, stage, write

CHECK = "checks/check_no_home_paths.py"


def run_check(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["python3", str(REPO_ROOT / CHECK), *args],
                          cwd=repo, capture_output=True, text=True)


def test_clean_tree_passes(repo):
    write(repo, "tool.sh", 'ROOT="$(cd "$(dirname "$0")/.." && pwd)"\nRULES="$ROOT/rules/canada.yaml"\n')
    stage(repo, "tool.sh")
    r = run_check(repo, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 staged files clean" in r.stdout


def test_each_shape_is_red_and_named(repo):
    shapes = {
        "a.swift": '"/Users/ztomer/.cache/cargo-target/release/lib.dylib"',
        "b.sh": 'FFI="$HOME/Projects/Finance/ZincTender/_rescued_taxes_ffi"',
        "c.sh": 'FFI="${HOME}/Projects/x"',
        "d.md": "calls the binary with `--config-dir ~/Projects/salary`",
        "e.py": 'p = "/home/deploy/app/config.yaml"',
    }
    for name, line in shapes.items():
        write(repo, name, line + "\n")
    stage(repo, *shapes)
    r = run_check(repo, "--staged")
    assert r.returncode != 0
    for name in shapes:
        assert f"{name}:1:" in r.stdout, (name, r.stdout)
    assert "path-ok: <reason>" in r.stdout


def test_a_url_or_module_path_is_not_a_home_path(repo):
    # `/Users/` inside a URL or an identifier chain is not a filesystem home.
    write(repo, "ok.md", "see https://api.example.com/Users/list and pkg.Users/home\n")
    stage(repo, "ok.md")
    r = run_check(repo, "--staged")
    assert r.returncode == 0, r.stdout


def test_marker_with_reason_suppresses_same_line_and_next_line(repo):
    write(repo, "dev.rs",
          '// path-ok: dev fallback, never consulted inside a bundle\n'
          'const DEV: &str = "/Users/me/src/x";\n'
          'const TWO: &str = "/Users/me/src/y"; // path-ok: same dev fallback\n')
    stage(repo, "dev.rs")
    r = run_check(repo, "--staged")
    assert r.returncode == 0, r.stdout


def test_bare_marker_suppresses_nothing(repo):
    write(repo, "dev.rs", 'const DEV: &str = "/Users/me/src/x"; // path-ok:\n')
    stage(repo, "dev.rs")
    r = run_check(repo, "--staged")
    assert r.returncode != 0, "an empty reason is an off switch, not an escape hatch"


def test_exclude_skips_matching_paths_only(repo):
    write(repo, "docs/history.md", "it used to run from ~/Projects/salary\n")
    write(repo, "src/x.sh", "cd ~/Projects/salary\n")
    stage(repo, "docs/history.md", "src/x.sh")
    r = run_check(repo, "--staged", "--exclude", "^docs/")
    assert r.returncode != 0
    assert "src/x.sh:1:" in r.stdout and "docs/history.md" not in r.stdout


def test_zero_files_is_a_refusal_not_a_pass(repo):
    r = run_check(repo, "--staged")
    assert r.returncode != 0
    assert "refusing to report clean over zero files" in r.stdout
