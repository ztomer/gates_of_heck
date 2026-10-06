"""The suite cannot drift back slow (roadmap 5B). Imported into conftest; acts at import and per test.

Each guard is deterministic -- a count or a refusal, never a wall-clock budget, because on a box
other sessions load, a timing gate flakes and gets switched off:

* a test that BUILDS goh behind the session's back fails at that build. `test_gate_environment`
  rebuilt it on every run (6 of its 10 s) by not passing GOH_BIN. A `cargo` shim first on every
  worker's PATH refuses a build in a gates_of_heck workspace unless the caller says the build is
  meant -- `DRIFT_BUILD_OK=1` in that build's environment: the session fixture's one build, and a
  test whose subject IS the build. Said at the call site, not by file, because such a test scrubs
  its environment (PYTEST_CURRENT_TEST with it), and the opt-in is then the one thing it passes;
* a test's call phase over CEILING_S fails -- 60 s, wide enough never to flake (honest tests
  reached 25-30 s with a push gate's coverage run and a second suite on the box at once), tight
  enough to catch a leaked child holding a captured pipe (one waited out a whole `sleep 30` -- a
  leak that long or longer) or a runaway build. A test that genuinely needs longer is named in
  SLOW with its own ceiling.

Found by the build guard on its first run: under GOH_LIVE with uncommitted Rust, every gate test
resolved the working tree's goh by BUILDING it (gates/_goh_bin.sh, `goh_live_binary`) -- 75 tests
racing one `cargo build --release` on cargo's lock. `prebuild_live` (conftest's
`pytest_configure`) now builds it once, in the
controller, before a worker starts; every gate then finds it current.

* a crate a test builds OUTSIDE this checkout builds into BUILD_DIR, a directory this run owns and
  removes. `~/.cargo/config.toml` keys `build-dir` by `{workspace-path-hash}`, so each temp
  workspace is a fresh build dir that removing the temp dir never reaches: 10,450 of them, 250 GB,
  filled the disk on 2026-10-06. The shim sets it, so a gate a test drives inherits it too; a
  caller that names its own build dir (the coverage gate does) keeps it.

The static ratchets (a `git init` per fixture, a pool sized to the machine) are in
tests/test_suite_drift.py.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

CEILING_S = 60.0
SLOW: dict[str, float] = {  # nodeid -> its own ceiling, each with its reason
    # a real `cargo build --release` of goh, cold in a fresh export: 30 s measured under load
    "tests/test_goh_build_publish.py::test_a_broken_uncommitted_edit_never_reaches_the_published_binary": 600,
    # builds the working tree's goh (GOH_LIVE) into its own target dir when that is cold
    "tests/test_binary_source_identity.py::test_goh_live_runs_the_working_trees_binary_not_heads": 600,
}

_REAL_CARGO = shutil.which("cargo")
BUILD_DIR = tempfile.mkdtemp(prefix="goh-test-cargo-build.")
atexit.register(shutil.rmtree, BUILD_DIR, True)
if _REAL_CARGO:
    _shim_dir = tempfile.mkdtemp(prefix="goh-drift-cargo.")
    atexit.register(shutil.rmtree, _shim_dir, True)  # one per worker per run, 42 had leaked
    with open(os.path.join(_shim_dir, "cargo"), "w", encoding="utf-8") as _f:
        _f.write(
            "#!/bin/sh\n"
            'dir="$PWD"; prev=""\n'
            'for a; do [ "$prev" = "--manifest-path" ] && dir="$(dirname "$a")"; prev="$a"; done\n'
            # A workspace outside this checkout is a throwaway: its build goes where the run removes it,
            # still one directory per workspace -- two estates' crates named `c` sharing one build dir
            # read each other's dep-info.
            # The template is assigned, never a `${VAR:-default}`: its `}` would close that expansion
            # and append a stray `}` to a build dir the caller named.
            f'case "$(cd "$dir" 2>/dev/null && pwd -P)/" in "{_ROOT.resolve()}/"*) ;; *) '
            '[ -n "${CARGO_BUILD_BUILD_DIR:-}" ] || export '
            f"CARGO_BUILD_BUILD_DIR='{BUILD_DIR}/{{workspace-path-hash}}' ;; esac\n"
            'case " $* " in *" build "*|*" test "*|*" run "*|*" install "*) ;; '
            f'*) exec "{_REAL_CARGO}" "$@" ;; esac\n'
            f'[ -n "${{DRIFT_BUILD_OK:-}}" ] && exec "{_REAL_CARGO}" "$@"\n'
            'test_file="${PYTEST_CURRENT_TEST%%::*}"\n'
            'while [ "$dir" != "/" ] && [ -n "$dir" ]; do\n'
            '  if [ -f "$dir/crates/goh/src/main.rs" ]; then\n'
            '    echo "✗ $test_file built goh outside the session fixture (cargo $*): pass the" \\\n'
            '         "session binary (GOH_BIN=native_goh_path()) -- tests/_drift_guard.py" >&2\n'
            "    exit 97\n"
            "  fi\n"
            '  dir="$(dirname "$dir")"\n'
            "done\n"
            f'exec "{_REAL_CARGO}" "$@"\n'
        )
    os.chmod(os.path.join(_shim_dir, "cargo"), 0o755)
    os.environ["PATH"] = f"{_shim_dir}{os.pathsep}{os.environ.get('PATH', '')}"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when != "call" or not rep.passed:
        return
    ceiling = SLOW.get(item.nodeid, CEILING_S)
    if call.duration > ceiling:
        rep.outcome = "failed"
        rep.longrepr = (
            f"{item.nodeid} took {call.duration:.1f} s, over its {ceiling:g} s ceiling "
            "(tests/_drift_guard.py): a leaked child holding a captured pipe, a hidden build, or a "
            "test that runs a whole gate to check one step. Fix it, or name it in SLOW with a reason."
        )


def prebuild_live(config) -> None:
    """Under GOH_LIVE, the working tree's goh -- built once, here, if its Rust differs from HEAD."""
    if hasattr(config, "workerinput") or not os.environ.get("GOH_LIVE"):
        return  # a worker: the controller already did it
    r = subprocess.run(
        ["bash", "-c", 'source "$1/gates/_goh_bin.sh"; goh_live_binary "$1"; [ -n "$goh_native" ] '
         '|| [ -z "$goh_native_why" ]', "_", str(_ROOT)],
        capture_output=True, text=True, timeout=900, check=False,
        env=dict(os.environ, DRIFT_BUILD_OK="1"),
    )  # fmt: skip
    if r.returncode != 0:
        raise pytest.UsageError(f"GOH_LIVE: the working tree's goh did not build:\n{r.stderr}")
