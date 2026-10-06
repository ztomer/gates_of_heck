"""The suite cannot drift back slow (roadmap 5B): the static half. tests/_drift_guard.py is the half
that acts while the suite runs, and the last two tests here prove it can fail.

Each ratchet is seeded with exactly today's sites. A NEW site fails; a site that goes away fails
too, until its entry is lowered or deleted -- an entry left above its count would let the next one
in unseen.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from conftest import REPO_ROOT

TESTS = REPO_ROOT / "tests"

# A test repo made by `git init` costs ~30 ms of git start-up per fixture; tests/_fast_git.py's
# `fast_init` copies a template `.git` instead. These remaining sites test init itself, build a
# repo `fast_init` cannot (bare, a hook's own environment), or are strings in a fixture's script.
GIT_INIT_SITES = {
    "conftest.py": 2,  # the templates fast_init and the `repo` fixture copy
    "test_hook_git_env.py": 7,  # init under a hook's GIT_* environment is the subject
    "test_cwd_and_py_gate.py": 3,
    "test_skills_corpus_external.py": 2,
    "test_required_tools_manifest.py": 2,
    "test_release_kit.py": 2,
    "test_release_hardening.py": 2,
    "test_install.py": 2,
    "test_goh_git_spawns.py": 2,
    "test_estate_corpus.py": 2,
    "test_binary_currency.py": 2,
    "test_tree_stamp.py": 1,
    "test_swift_gate_project_selection.py": 1,
    "test_session_bench.py": 1,
    "test_rust_lint_configs.py": 1,
    "test_rust_gate.py": 1,
    "test_rust_each_crate.py": 1,
    "test_required_tools.py": 1,
    "test_line_cap_off.py": 1,
    "test_hook_currency.py": 1,
    "test_goh_prefetch.py": 1,
    "test_goh_bin_once.py": 1,
    "test_gitutil_paths.py": 1,
    "test_gates_from_head.py": 1,
    "test_gate_runtime_path.py": 1,
    "test_gate_calibration.py": 1,
    "test_estate_corpus_cache.py": 1,
    "test_check_swift_warnings.py": 1,
    "test_check_probes_pass.py": 1,
    "test_check_no_home_paths.py": 1,
    "test_binary_source_identity.py": 1,
    "test_round.py": 1,  # a BARE remote, which the template cannot be
}

# A pool sized to the machine runs inside every one of the suite's workers at once: 12 workers x
# cpu_count threads, each spawning processes. New pools take an explicit bound.
CPU_COUNT_POOLS = {
    "checks/_ordered_pool.py": 1,
    "checks/check_probes_pass.py": 1,
}

_INIT = re.compile(r'"init"|git init')


def _tracked(*specs: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", *specs], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return out.stdout.split()


def _count(pattern: re.Pattern[str], paths: list[str]) -> Counter[str]:
    found: Counter[str] = Counter()
    for rel in paths:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        n = sum(1 for line in text.splitlines() if pattern.search(line))
        if n:
            found[rel] = n
    return found


def _ratchet(found: Counter[str], allowed: dict[str, int], what: str) -> None:
    over = {f: n for f, n in found.items() if n > allowed.get(f, 0)}
    stale = {f: (allowed[f], found.get(f, 0)) for f in allowed if found.get(f, 0) < allowed[f]}
    assert not over, f"new {what} (tests/test_suite_drift.py): {over}"
    assert not stale, f"{what} ratchet above its count -- lower or delete the entry: {stale}"


def test_no_new_git_init_in_tests() -> None:
    # The template's home, and the two files that police the pattern (they name it, never run it).
    own = ("_fast_git.py", "_drift_guard.py", "test_suite_drift.py")
    paths = [p for p in _tracked("tests/*.py") if not p.endswith(own)]
    found = _count(_INIT, paths)
    _ratchet(Counter({Path(f).name: n for f, n in found.items()}), GIT_INIT_SITES, "git init site")


def test_no_new_pool_sized_to_the_machine() -> None:
    found = _count(re.compile(r"cpu_count\(\)"), _tracked("*.py"))
    found = Counter({f: n for f, n in found.items() if not f.startswith("tests/")})
    _ratchet(found, CPU_COUNT_POOLS, "cpu_count() pool")


def _planted(tmp_path: Path, body: str, ceiling: float = 30) -> subprocess.CompletedProcess[str]:
    """A one-test suite whose conftest is the real guard's, run in a child pytest."""
    (tmp_path / "conftest.py").write_text(
        f"import sys\nsys.path.insert(0, {str(TESTS)!r})\n"
        "import _drift_guard\n"
        "from _drift_guard import pytest_runtest_makereport  # noqa: F401\n"
        f"_drift_guard.CEILING_S = {ceiling}\n"
    )
    (tmp_path / "test_planted.py").write_text(body)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
         str(tmp_path)],
        capture_output=True, text=True, cwd=tmp_path, timeout=60, check=False,
    )  # fmt: skip


def test_a_test_over_its_ceiling_fails(tmp_path: Path) -> None:
    r = _planted(tmp_path, "import time\n\ndef test_slow():\n    time.sleep(0.6)\n", ceiling=0.3)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "over its 0.3 s ceiling" in r.stdout, r.stdout


def test_a_test_that_builds_goh_fails_and_other_cargo_passes(tmp_path: Path) -> None:
    manifest = REPO_ROOT / "Cargo.toml"
    r = _planted(
        tmp_path,
        "import subprocess\n\n"
        "def test_metadata_passes():\n"
        f"    r = subprocess.run(['cargo', 'metadata', '--no-deps', '--format-version', '1',\n"
        f"                        '--manifest-path', {str(manifest)!r}], capture_output=True)\n"
        "    assert r.returncode == 0\n\n"
        "def test_builds_goh():\n"
        f"    r = subprocess.run(['cargo', 'build', '-p', 'goh', '--manifest-path', {str(manifest)!r}],\n"
        "                       capture_output=True, text=True)\n"
        "    assert r.returncode == 97, r.stderr\n"
        "    assert 'built goh outside the session fixture' in r.stderr\n",
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_throwaway_crate_builds_into_the_runs_own_build_dir(tmp_path: Path) -> None:
    """A crate a test builds in a temp dir must not leave its build behind in cargo's shared build
    root. `~/.cargo/config.toml` keys `build-dir` by `{workspace-path-hash}`, so every temp workspace
    is a new, never-reused build dir that deleting the temp dir does not reach: 10,450 of them, 250 GB,
    filled the disk on 2026-10-06. The suite's cargo shim points every workspace outside this checkout
    at a build dir the run owns and removes."""
    import os
    import shutil
    import uuid

    if shutil.which("cargo") is None:
        return
    name = f"leakprobe{uuid.uuid4().hex[:10]}"
    crate = tmp_path / name
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "0.0.0"\nedition = "2021"\n', encoding="utf-8"
    )
    (crate / "src" / "lib.rs").write_text("", encoding="utf-8")
    r = subprocess.run(["cargo", "build", "-q"], cwd=crate, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    shared = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo")) / "build"
    leaked = list(shared.glob(f"*/*/debug/.fingerprint/{name}-*"))
    assert not leaked, f"the throwaway crate's build landed in the shared root: {leaked}"
    import _drift_guard

    owned = list(Path(_drift_guard.BUILD_DIR).glob(f"**/.fingerprint/{name}-*"))
    assert owned, f"the build is not in the run's own build dir {_drift_guard.BUILD_DIR}"
    # A caller that names its own build dir (the coverage gate pins one per project) keeps it, exactly.
    named = tmp_path / "named-build"
    r = subprocess.run(
        ["cargo", "build", "-q"],
        cwd=crate,
        capture_output=True,
        text=True,
        env=dict(os.environ, CARGO_BUILD_BUILD_DIR=str(named)),
    )
    assert r.returncode == 0, r.stderr
    assert list(named.glob(f"*/.fingerprint/{name}-*")), "a caller's own build dir was not kept"
