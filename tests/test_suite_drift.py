"""The suite cannot drift back slow (roadmap 5B): the static half. tests/_drift_guard.py is the half
that acts while the suite runs, and the last two tests here prove it can fail.

Each ratchet is seeded with exactly today's sites. A NEW site fails; a site that goes away fails
too, until its entry is lowered or deleted -- an entry left above its count would let the next one
in unseen.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections import Counter
from pathlib import Path

import _drift_guard
import pytest
from _drift_planted import planted, planted_load, took_over_ceiling
from conftest import REPO_ROOT

TESTS = REPO_ROOT / "tests"

# A test repo made by `git init` costs ~30 ms of git start-up per fixture; tests/_fast_git.py's
# `fast_init` copies a template `.git` instead. These remaining sites test init itself, build a
# repo `fast_init` cannot (bare, a hook's own environment), or are strings in a fixture's script.
GIT_INIT_SITES = {
    "conftest.py": 2,  # the templates fast_init and the `repo` fixture copy
    "test_hook_git_env.py": 7,  # init under a hook's GIT_* environment is the subject
    "test_commit_hook_git_env.py": 2,  # the scratch suite's init under a real hook; a bare repo
    "test_cwd_and_py_gate.py": 3,
    "test_skills_corpus_external.py": 2,
    "test_required_tools_manifest.py": 2,
    "test_release_kit.py": 2,
    "test_release_hardening.py": 2,
    "test_install.py": 1,
    "test_goh_git_spawns.py": 2,
    "test_estate_corpus.py": 2,
    "test_binary_currency.py": 1,
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
    # Not a pool: the default COUNT of machine-wide gate slots (one gate per eight cores), which
    # bounds the gates and so every pool they run.
    "lib/gate_slot.py": 1,
    # Not a pool: the core count load.py REFUSES more spinners than (G1c), without --oversubscribe.
    "tools/load.py": 1,
    # Not a pool: the core count an agent's outcome line records beside the load average, which the
    # weekly usage review judges per core (GOH.ULTRARULES).
    "hooks/claude/agent_guard.py": 1,
    # Not a pool: the core count `goh capacity` divides the load average by, the lane-start rule's
    # load per core (GOH.ADAPTLANES).
    "lib/capacity.py": 1,
}

_INIT = re.compile(r'"init"|git init')


def _tracked(*specs: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", *specs],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
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


@pytest.mark.load_sensitive
def test_no_new_git_init_in_tests() -> None:
    # The template's home, and the two files that police the pattern (they name it, never run it).
    own = ("_fast_git.py", "_drift_guard.py", "test_suite_drift.py")
    paths = [p for p in _tracked("tests/*.py") if not p.endswith(own)]
    found = _count(_INIT, paths)
    _ratchet(Counter({Path(f).name: n for f, n in found.items()}), GIT_INIT_SITES, "git init site")


@pytest.mark.load_sensitive
def test_no_new_pool_sized_to_the_machine() -> None:
    found = _count(re.compile(r"cpu_count\(\)"), _tracked("*.py"))
    found = Counter({f: n for f, n in found.items() if not f.startswith("tests/")})
    _ratchet(found, CPU_COUNT_POOLS, "cpu_count() pool")


SLEEPER = "import time\n\ndef test_slow():\n    time.sleep(0.6)\n"


def test_a_test_over_its_ceiling_fails(tmp_path: Path) -> None:
    r = planted(tmp_path, SLEEPER, ceiling=0.3)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "over its 0.3 s ceiling" in r.stdout, r.stdout


def test_the_ceiling_stretches_with_the_boxs_load(tmp_path: Path) -> None:
    """A 15 s test took 63.8 s at load 38 on 16 cores and refused a push (bigkoff, 2026-10-08): a
    fixed wall-clock bound on a box other sessions load is the flake the docstring says it is not."""
    r = planted(tmp_path, SLEEPER, ceiling=0.3, load=4.0)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_stretched_ceiling_still_fails_and_says_by_how_much(tmp_path: Path) -> None:
    r = planted(tmp_path, SLEEPER, ceiling=0.3, load=1.5)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "over its 0.3 s ceiling x1.5 for the box's load and speed" in r.stdout, r.stdout


def test_the_load_factor_is_run_queue_per_core_never_below_one(monkeypatch) -> None:
    monkeypatch.setenv(_drift_guard.SPEED_ENV, "1")  # the owner's box: the load alone
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    monkeypatch.setattr(os, "getloadavg", lambda: (38.0, 30.0, 20.0))
    assert _drift_guard.load_factor() == 38.0 / 16
    monkeypatch.setattr(os, "getloadavg", lambda: (4.0, 5.0, 6.0))
    assert _drift_guard.load_factor() == 1.0
    monkeypatch.setattr(os, "getloadavg", lambda: (13.0, 64.0, 70.0))  # a load that fell mid-test
    assert _drift_guard.load_factor() == 64.0 / 16


def test_a_slower_box_stretches_every_bound_by_its_measured_speed(monkeypatch) -> None:
    """GOH.CIWALL: the hosted runner's cores are slower than the owner's; its bounds stretch by
    the measured ratio, never below 1 nor above the cap, measured once and then read."""
    # the instrument itself, first: the real probe reads a CPU time, well under a second
    assert 0 < _drift_guard._probe_cpu_s() < 1.0
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    monkeypatch.setattr(os, "getloadavg", lambda: (32.0, 30.0, 20.0))
    monkeypatch.delenv(_drift_guard.SPEED_ENV, raising=False)
    calls = []
    slow = _drift_guard.REFERENCE_CPU_S * 2.5
    monkeypatch.setattr(_drift_guard, "_probe_cpu_s", lambda: calls.append(1) or slow)
    assert _drift_guard.load_factor() == pytest.approx(2.0 * 2.5)
    assert _drift_guard.load_factor() == pytest.approx(2.0 * 2.5) and calls == [1]  # read, once
    for probe, want in ((_drift_guard.REFERENCE_CPU_S / 3, 1.0), (1e9, _drift_guard.SPEED_CAP)):
        monkeypatch.delenv(_drift_guard.SPEED_ENV)
        monkeypatch.setattr(_drift_guard, "_probe_cpu_s", lambda p=probe: p)
        assert _drift_guard.speed_factor() == want


def test_a_load_that_fell_before_the_test_ended_still_stretches_its_ceiling(tmp_path) -> None:
    """GOH.CEILINGHEADROOM: the ceiling was stretched by the load read when the test ENDED, so a
    test that ran its whole call at load 64 on 16 cores (4x) and ended as the box went quiet was
    judged as if it ran on a quiet one. The load is sampled through the run; a ceiling of 0.3 s
    x4 holds a 0.6 s call."""
    body = (
        "import os, time\n\ndef test_slow():\n"
        "    os.environ['FAKE_LOAD_PHASE'] = 'run'\n    time.sleep(0.6)\n"
        "    os.environ['FAKE_LOAD_PHASE'] = 'end'\n    time.sleep(0.05)\n"
    )
    r = planted_load(tmp_path, body)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_reported_duration_is_parsed_not_pinned() -> None:
    line = "test_b_slow_on_a_quiet_box took {} s, over its 0.3 s ceiling"
    for shown in ("0.6", "0.7", "12.4"):  # a quiet box, a traced box, a starved one
        assert took_over_ceiling(line.format(shown), "test_b_slow_on_a_quiet_box", 0.3) == float(
            shown
        )
    assert (
        took_over_ceiling("test_b_slow_on_a_quiet_box passed", "test_b_slow_on_a_quiet_box", 0.3)
        is None
    )
    assert took_over_ceiling(line.format("0.6"), "test_other", 0.3) is None


def test_a_2x_slowdown_at_low_load_still_fails_after_a_loaded_test(tmp_path) -> None:
    """The other half: a call twice its ceiling on a quiet box fails, however loaded the box was
    BEFORE it started -- the test before it ran at load 64 (and passed, x4), and its samples must
    not stretch the quiet call that followed."""
    body = (
        "import os, time\n\n"
        "def test_a_loaded_one():\n    time.sleep(0.8)\n\n"
        "def test_b_slow_on_a_quiet_box():\n"
        "    os.environ['FAKE_LOAD_PHASE'] = 'end'\n    time.sleep(0.6)\n"
    )
    r = planted_load(tmp_path, body)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "1 failed, 1 passed" in r.stdout, r.stdout
    took = took_over_ceiling(r.stdout, "test_b_slow_on_a_quiet_box", 0.3)
    assert took is not None, r.stdout
    assert took >= 0.6, r.stdout  # the call slept 0.6 s; coverage tracing only adds to it


def test_a_test_that_builds_goh_fails_and_other_cargo_passes(tmp_path: Path) -> None:
    manifest = REPO_ROOT / "Cargo.toml"
    r = planted(
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
    r = subprocess.run(
        ["cargo", "build", "-q"],
        cwd=crate,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    shared = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo")) / "build"

    # A unit's trace, in both build-dir layouts: `.fingerprint/<name>-<hash>` and, from cargo 1.100,
    # `build/<name>/<hash>/fingerprint` (one directory per unit).
    def units(d: Path, pre: str) -> list[Path]:
        return [*d.glob(f"{pre}.fingerprint/{name}-*"), *d.glob(f"{pre}build/{name}/*/fingerprint")]

    leaked = units(shared, "*/*/debug/")
    assert not leaked, f"the throwaway crate's build landed in the shared root: {leaked}"
    import _drift_guard

    owned = units(Path(_drift_guard.BUILD_DIR), "**/")
    assert owned, f"the build is not in the run's own build dir {_drift_guard.BUILD_DIR}"
    # A caller that names its own build dir (the coverage gate pins one per project) keeps it, exactly.
    named = tmp_path / "named-build"
    r = subprocess.run(
        ["cargo", "build", "-q"],
        cwd=crate,
        capture_output=True,
        text=True,
        env=dict(os.environ, CARGO_BUILD_BUILD_DIR=str(named)),
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert units(named, "*/"), "a caller's own build dir was not kept"


def _rustc_env_names(
    crate: Path, wrapper_log: Path, argv: list[str], env: dict[str, str]
) -> set[str]:
    """The CARGO_* names rustc inherits when `argv` builds `crate`, read by a recording wrapper."""
    wrapper = wrapper_log.with_suffix(".sh")
    wrapper.write_text(
        f'#!/bin/sh\nenv | grep -E "^(CARGO_|GOHPROBE=)" | cut -d= -f1 >> "{wrapper_log}"\n'
        'exec "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    r = subprocess.run(
        argv,
        cwd=crate,
        capture_output=True,
        text=True,
        env={**env, "RUSTC_WRAPPER": str(wrapper)},
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert wrapper_log.exists(), (
        f"{argv}: the recording wrapper never ran, the instrument saw nothing"
    )
    return set(wrapper_log.read_text(encoding="utf-8").split())


@pytest.mark.parametrize("sub", ["build", "clippy", "--config"])
def test_the_shim_keeps_its_build_dir_out_of_rustcs_environment(tmp_path: Path, sub: str) -> None:
    """The shim's build-dir and no-incremental reach cargo on its COMMAND LINE, never in the
    environment rustc inherits (GOH.SCCACHEENV). sccache 0.19 hashes every CARGO_* variable it sees
    into the Rust key; the exported per-run build-dir made every suite run's compiles a fresh key,
    68% of the machine's Rust misses (design GOH.CACHEEVICT section 4, F1). A recording
    RUSTC_WRAPPER stands in for sccache; the baseline is the real cargo on the same crate, so what
    cargo and the user's `[env]` set is not charged to the shim. `--config`: a caller's own
    `--config` before the subcommand survives the shim's (cargo keeps one list of the two)."""
    import shutil
    import uuid

    if shutil.which("cargo") is None or _drift_guard._REAL_CARGO is None:
        return
    name = f"envprobe{uuid.uuid4().hex[:10]}"
    crate = tmp_path / name
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "0.0.0"\nedition = "2021"\n', encoding="utf-8"
    )
    (crate / "src" / "lib.rs").write_text("pub fn f() {}\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CARGO_") or k == "CARGO_HOME"}
    plain = _rustc_env_names(
        crate,
        tmp_path / "plain.log",
        [
            _drift_guard._REAL_CARGO,
            "clippy" if sub == "clippy" else "build",
            "-q",
            "--config",
            f"build.build-dir='{tmp_path / 'b'}'",
        ],
        env,
    )
    argv = ["cargo", sub, "-q"]
    if sub == "--config":
        argv = ["cargo", "--config", "env.GOHPROBE='1'", "build", "-q"]
        sub = "build"
    shimmed = _rustc_env_names(crate, tmp_path / "shim.log", argv, env)
    if argv[1] == "--config":
        assert "GOHPROBE" in shimmed, "the shim's --config dropped the caller's own"
    added = sorted(shimmed - plain - {"GOHPROBE"})
    assert not added, f"cargo {sub}: the shim put {added} in rustc's environment, into every key"
    own = Path(_drift_guard.BUILD_DIR)
    built = [*own.glob(f"**/.fingerprint/{name}-*"), *own.glob(f"**/build/{name}/*/fingerprint")]
    assert built, f"cargo {sub} did not build in the run's own build dir {_drift_guard.BUILD_DIR}"
    sessions = [*own.glob(f"**/incremental/{name}-*")]
    assert not sessions, f"cargo {sub} built the throwaway incrementally: {sessions}"


def test_a_workspace_under_home_is_not_mistaken_for_one_naming_its_build_dir() -> None:
    """The shim keeps a workspace's OWN build-dir (a `.cargo/config.toml` at or above it), and it
    walked up into `$HOME/.cargo/config.toml` -- the user's GLOBAL config, which keys `build-dir` by
    `{workspace-path-hash}` in the shared root -- for any workspace under HOME. build-goh.sh's
    export lives under HOME (`$HOME/.cache/goh/build/<root>/src`), so the publish test built goh
    cold into the shared root on every run, in a leaf no run removes (GOH.EA2 item 2)."""
    import shutil
    import tempfile

    if shutil.which("cargo") is None:
        return
    ws = Path(tempfile.mkdtemp(prefix="ws.", dir=Path.home() / ".cache"))
    try:
        (ws / "src").mkdir()
        (ws / "Cargo.toml").write_text(
            '[package]\nname = "w"\nversion = "0.0.0"\nedition = "2021"\n'
        )
        (ws / "src" / "lib.rs").write_text("")
        env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "DRIFT_BUILD_OK": "1"}
        meta = subprocess.run(
            ["cargo", "build", "--message-format", "json", "-q"],
            cwd=ws, capture_output=True, text=True, env=env, check=False, stdin=subprocess.DEVNULL,
        )  # fmt: skip
        assert meta.returncode == 0, meta.stderr
        built = [ln for ln in meta.stdout.splitlines() if '"filenames"' in ln]
        assert built and all(_drift_guard.BUILD_DIR in ln for ln in built), built
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def test_the_controller_resolves_the_binary_every_gate_will_ask_for(tmp_path):
    """G2c: a checkout whose bin/goh is not HEAD's build (a fresh worktree, a commit whose crates
    moved) made the FIRST gate a test spawned build goh -- and the drift guard refuses a build behind
    the session's back, so every gate step that runs goh failed (9 of test_rust_gate.py, in any new
    worktree). The controller resolves what the gates resolve, once, before a worker starts: the
    working tree's build under GOH_LIVE with a Rust delta, otherwise HEAD's."""
    gates = tmp_path / "gates"
    gates.mkdir()
    called = tmp_path / "resolved"
    # No Rust delta (goh_live_binary does not apply), and bin/goh is not HEAD's build.
    (gates / "_goh_bin.sh").write_text(
        "goh_live_binary() { return 1; }\n"
        f'goh_resolve_native() {{ : > "{called}"; goh_native=/bin/true; goh_native_why=""; }}\n'
    )

    class _Config:  # the controller: no workerinput
        pass

    _drift_guard.prebuild_live(_Config(), root=tmp_path)
    assert called.exists(), "the controller left the first gate to build goh behind the session"
