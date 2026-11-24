"""The suite cannot drift back slow (roadmap 5B). Imported into conftest; acts at import and per test.

Each guard is deterministic -- a count or a refusal, never a wall-clock budget, because on a box
other sessions load, a timing gate flakes and gets switched off:

* a test that BUILDS goh behind the session's back fails at that build. `test_gate_environment`
  rebuilt it on every run (6 of its 10 s) by not passing GOH_BIN. A `cargo` shim first on every
  worker's PATH refuses a build in a gates_of_heck workspace unless the caller says the build is
  meant -- `DRIFT_BUILD_OK=1` in that build's environment: the session fixture's one build, and a
  test whose subject IS the build. Said at the call site, not by file, because such a test scrubs
  its environment (PYTEST_CURRENT_TEST with it), and the opt-in is then the one thing it passes;
* a test's setup over SETUP_CEILING_S fails the same way (GOH.SETUPCEILING, below);
* a test's call phase over CEILING_S fails -- 60 s on a box at most one runnable thread per
  core, tight enough to catch a leaked child holding a captured pipe (one waited out a whole
  `sleep 30` -- a leak that long or longer) or a runaway build. A wall-clock bound IS a timing
  gate, so it stretches by `load_factor()`, the run queue per core: a 15 s test took 63.8 s at
  load 38 on 16 cores and refused a push (2026-10-08). The load is the one DURING the call --
  sampled through it (`_sample`), never only the reading when it ended (GOH.CEILINGHEADROOM). A
  test that genuinely needs longer is named in SLOW with its own ceiling, stretched the same way.

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
import resource
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import pytest
from _reused_tmp import reused_mkdtemp

_ROOT = Path(__file__).resolve().parents[1]

CEILING_S = 60.0
# A test's SETUP phase has its own ceiling (GOH.SETUPCEILING): pytest's setup report covers every
# fixture set up for that test, and a module or session fixture is charged once, to the first test
# that triggers it. Only the call was judged, so test_self_walk's module fixture spent 45.5 s on two
# `structural --full` walks that no gate saw. Which test is first depends on the worker and the
# order, so a module's own setup ceiling is keyed by its FILE, never by a test id that moves.
# 20 s: the dearest ordinary fixtures (test_gate_temp_leaks' structural walk and the module
# fixtures of test_gate_calibration and test_rust_gate_scoped_cache) run 4-8 s on a quiet box,
# read as a loaded run's seconds over the load factor the guard stretches by (2026-11-20, load
# 45-90 on 16 cores: 54 s / x7, 14 s / x2.8, 13 s / x2.8); 0.8 of 20 is 16 s, twice the dearest
# of them. A leaked child or a fixture that gates a whole tree to prove one thing waits 30 s+,
# over this at any load.
SETUP_CEILING_S = 20.0
# test FILE -> its own setup ceiling, each with its reason. Empty since GOH.SELFWALKFILTER:
# test_self_walk's module fixture walked goh twice (45.5 s, GOH.CEILINGHEADROOM2); it clones the
# two trees now, and each part's walk is its own test's call.
SETUP_SLOW: dict[str, float] = {}


def setup_ceiling(nodeid: str) -> float:
    return SETUP_SLOW.get(nodeid.split("::", 1)[0], SETUP_CEILING_S)


SLOW: dict[str, float] = {  # nodeid -> its own ceiling, each with its reason
    # a real `cargo build --release` of goh, cold in a fresh export: 30 s measured under load
    "tests/test_goh_build_publish.py::test_a_broken_uncommitted_edit_never_reaches_the_published_binary": 600,
    # builds the working tree's goh (GOH_LIVE) into its own target dir when that is cold
    "tests/test_binary_source_identity.py::test_goh_live_runs_the_working_trees_binary_not_heads": 600,
    # builds and gates real crates, then re-gates after an edit: 73 s alone at load 13 (2026-10-08)
    "tests/test_rust_gate_scoped_cache.py::test_only_the_edited_crate_and_its_users_are_re_gated": 300,
}


# THE BOX'S SPEED (GOH.CIWALL). Every bound here was set on the owner's machine (an M4 Max) and
# stretched by its load alone, so a slower core read as a slow test: the 3-core hosted runner ran
# test_gate_temp_leaks' gate in 299.9 s against its 60 s ceiling x4.3 for the load (v0.139.2 and
# v0.139.3 went red). The per-core speed is MEASURED, once per suite, as the CPU time -- not the
# wall, which the load already stretches -- of a fixed loop, against what the same loop took on
# the owner's box under a gate's load (REFERENCE_CPU_S, 0.0143-0.0170 s best of 5, 2026-11-19).
# The controller measures it at import, before a worker starts, and every process under it reads
# that one figure (SPEED_ENV), so no worker re-measures it under the suite's own load. Never below
# 1 (the owner's box is the reference), never above SPEED_CAP (a probe that misread a pathological
# box must not switch the ceilings off).
SPEED_ENV = "GATES_OF_HECK_SUITE_SPEED"  # not GOH_-prefixed: no env scrub drops it
REFERENCE_CPU_S = 0.016
SPEED_CAP = 4.0


def _loop() -> int:
    """The fixed loop both probes time: REFERENCE_CPU_S of one core of the owner's box."""
    x = 0
    for i in range(400_000):
        x += i * i % 7
    return x


def _probe_cpu_s() -> float:
    """The best of 5 CPU times of one fixed loop (process time: blind to the run queue)."""
    best = float("inf")
    for _ in range(5):
        t0 = time.process_time()
        _loop()
        best = min(best, time.process_time() - t0)
    return best


def speed_factor() -> float:
    """How much slower than the owner's box this box's cores run: measured once, then read."""
    got = os.environ.get(SPEED_ENV, "")
    try:
        return min(SPEED_CAP, max(1.0, float(got)))
    except ValueError:
        pass
    factor = min(SPEED_CAP, max(1.0, _probe_cpu_s() / REFERENCE_CPU_S))
    os.environ[SPEED_ENV] = f"{factor:.3f}"
    return factor


def load_factor(start: float | None = None, stop: float | None = None) -> float:
    """How much slower than quiet this box runs a test: the run queue per core, never below 1,
    times its cores' speed against the owner's (`speed_factor`). Read when the test ends, as the
    larger of the 1- and 5-minute averages: a 197 s test that ran at load 180 ended at 13 and was
    judged by the 1-minute figure alone (2026-10-08). Given the call's window (epoch seconds), the
    mean of the 1-minute samples taken inside it counts too, when larger: the end reading alone
    misses a load that fell before the test ended (GOH.CEILINGHEADROOM). Samples from before the
    window never count, so a loaded setup does not stretch a slow call on a quiet box."""
    one, five, _ = os.getloadavg()
    load = max(one, five)
    if start is not None and stop is not None:
        with _SAMPLES_LOCK:
            during = [n for t, n in _SAMPLES if start <= t <= stop]
        if during:
            load = max(load, sum(during) / len(during))
    return max(1.0, load / (os.cpu_count() or 1)) * speed_factor()


# THE LOAD DURING THE CALL (GOH.CEILINGHEADROOM). test_land_identity ran 75.5 s against its 60 s
# ceiling stretched x1.3 by the load read as it ended (2026-11-20, GOH.PRESTEPMOVED's land): one
# reading, at the one moment the run had already left. A daemon thread per test process samples
# the 1-minute average every SAMPLE_S (the kernel updates it every 5 s), an hour of them kept;
# `load_factor` averages those inside a call's window. Started by the first report (a setup's,
# before any call), so a conftest that sets SAMPLE_S first is obeyed.
SAMPLE_S = 2.0
_SAMPLES: deque[tuple[float, float]] = deque(maxlen=1800)
_SAMPLES_LOCK = threading.Lock()
_sampler: list[threading.Thread] = []


def _sample() -> None:
    while True:
        try:
            sample = (time.time(), os.getloadavg()[0])
        except OSError:  # no load average on this box: the end reading decides, as it always did
            return
        with _SAMPLES_LOCK:
            _SAMPLES.append(sample)
        time.sleep(SAMPLE_S)


def _start_sampler() -> None:
    if not _sampler:
        _sampler.append(threading.Thread(target=_sample, name="drift-load-sampler", daemon=True))
        _sampler[0].start()


speed_factor()  # the controller's measurement, before any worker or child inherits the variable


sys.path.insert(0, str(_ROOT / "lib"))
try:
    from long_pole import QUIET_LOAD, pole  # a phase's quiet seconds, for the long-pole table
    from tool_shim import SHIM_MARK  # the one spelling the identity reads
finally:
    sys.path.pop(0)
_REAL_CARGO = shutil.which("cargo")
BUILD_DIR = reused_mkdtemp("goh-test-cargo-build.")
atexit.register(shutil.rmtree, BUILD_DIR, True)
if _REAL_CARGO:
    _shim_dir = reused_mkdtemp("goh-drift-cargo.")
    atexit.register(shutil.rmtree, _shim_dir, True)  # one per worker per run, 42 had leaked
    with open(os.path.join(_shim_dir, "cargo"), "w", encoding="utf-8") as _f:
        _f.write(
            "#!/bin/sh\n"
            # The verdict identity keys the cargo this runs, not this per-run file: keyed by its
            # own path, every suite run had its own tools key (lib/tool_shim.py).
            f"{SHIM_MARK}{_REAL_CARGO}\n"
            'dir="$PWD"; prev=""; named_bd=""; named_inc=""\n'
            'for a; do [ "$prev" = "--manifest-path" ] && dir="$(dirname "$a")"; prev="$a"\n'
            # A build-dir already on the command line -- the caller's, or an outer shim's (an xdist
            # worker's PATH holds the controller's shim behind its own) -- is kept, as the
            # variable was: cargo keeps the LAST of two, and an outer shim's would land after.
            '  case "$a" in *build.build-dir=*) named_bd=1 ;;\n'
            "  *build.incremental=*) named_inc=1 ;; esac\n"
            "done\n"
            # A workspace outside this checkout is a throwaway: its build goes where the run removes it,
            # still one directory per workspace -- two estates' crates named `c` sharing one build dir
            # read each other's dep-info.
            # The template is assigned, never a `${VAR:-default}`: its `}` would close that expansion
            # and append a stray `}` to a build dir the caller named.
            # A workspace that names its own build-dir (a .cargo/config.toml at or above it) keeps
            # it: the variable outranks every config file, so exporting it would replace the
            # fixture's choice -- the defect push_gate.sh's fallback build-dir had.
            # CARGO_HOME's config is the user's GLOBAL one, not the workspace's: a workspace under
            # HOME walks up into `$HOME/.cargo`, and taking its shared-root build-dir for the
            # workspace's own sent build-goh's export (under $HOME/.cache) to the shared root.
            'cargo_home="$(cd "${CARGO_HOME:-$HOME/.cargo}" 2>/dev/null && pwd -P)"\n'
            'own_build_dir() { d="$1"; while :; do\n'
            '  if [ "$(cd "$d/.cargo" 2>/dev/null && pwd -P)" != "$cargo_home" ]; then\n'
            '  for c in "$d/.cargo/config.toml" "$d/.cargo/config"; do\n'
            '  [ -f "$c" ] && grep -Eq "^[[:space:]]*build-dir[[:space:]]*=" "$c" && return 0; done; fi\n'
            '  [ "$d" = / ] && return 1; d="$(dirname "$d")"; done; }\n'
            'bd=""; inc=""\n'
            f'case "$(cd "$dir" 2>/dev/null && pwd -P)/" in "{_ROOT.resolve()}/"*) ;; *) '
            '[ -n "${CARGO_BUILD_BUILD_DIR:-}$named_bd" ] '
            '|| own_build_dir "$(cd "$dir" 2>/dev/null && pwd -P)" '
            f"|| bd='{BUILD_DIR}/{{workspace-path-hash}}'\n"
            # ...and builds it whole, never incrementally (GOH.FSCHURN3): a throwaway is built once,
            # so an incremental session dir is only files to make and remove (and sccache caches
            # no incremental build).
            '[ -n "${CARGO_INCREMENTAL:-}$named_inc" ] || inc=1 ;; esac\n'
            # ON THE COMMAND LINE, NEVER IN THE ENVIRONMENT (GOH.SCCACHEENV). sccache 0.19 hashes
            # every CARGO_* variable rustc inherits into its key, and an exported build-dir is a
            # path per run: 68% of the machine's Rust misses. Cargo keeps `--config` to itself.
            # It goes right AFTER the subcommand's name, unless the caller put its own `--config`
            # before it. Cargo 1.100 keeps one of the two lists: any `--config` after the name
            # drops every one before it (an incremental session was still written), and `clippy`
            # drops a list before its name, forwarding one after it to `cargo check` (probed).
            # Any other external subcommand keeps the environment, a named deviation.
            'c1=""; c2=""; c3=""; c4=""\n'
            '[ -n "$bd" ] && c1=--config && c2="build.build-dir=\'$bd\'"\n'
            '[ -n "$inc" ] && c3=--config && c4=build.incremental=false\n'
            'if [ -n "$bd$inc" ]; then n=$#; prev=""; sub=""; pre=""\n'
            "for a; do\n"
            '  [ -z "$sub" ] && case "$a" in --config|--config=*) pre=1 ;; esac\n'
            '  if [ -z "$sub" ]; then case "$prev" in -Z|--config|-C|--color) ;; *)\n'
            '  case "$a" in -*|+*) ;; *) sub="$a"\n'
            '    case "$a" in\n'
            "    build|b|check|c|test|t|run|r|bench|doc|d|rustc|rustdoc|install|fix|metadata|tree"
            "|clippy)\n"
            '      if [ -n "$pre" ]; then set -- "$@" ${c1:+"$c1"} ${c2:+"$c2"} '
            '${c3:+"$c3"} ${c4:+"$c4"} "$a"\n'
            '      else set -- "$@" "$a" ${c1:+"$c1"} ${c2:+"$c2"} ${c3:+"$c3"} ${c4:+"$c4"}; fi\n'
            '      prev="$a"; continue ;;\n'
            '    *) [ -n "$bd" ] && export CARGO_BUILD_BUILD_DIR="$bd"\n'
            '      [ -n "$inc" ] && export CARGO_INCREMENTAL=0 ;;\n'
            "    esac ;; esac ;; esac; fi\n"
            '  set -- "$@" "$a"; prev="$a"\n'
            'done; shift "$n"; fi\n'
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


# THE LONG-POLE READING (GOH.LONGPOLELOAD). Every phase's report carries `goh_pole`, its seconds
# on a quiet box (lib/long_pole.py `pole`): its wall, the CPU of this process and of the children
# it waited for over the same phase, and the stretch measured on this thread (below). xdist ships
# a report's attributes to the controller, where tests/_schedule.py sums a test's phases into the
# long-pole table the ratchet judges -- not the wall, which reads the box's load as the test's.
#
# THE STRETCH is MEASURED, never read from the load average: at load 29 on 16 cores a test burning
# one core ran 1.50 s of wall for 1.50 s of CPU, while `load_factor` said x1.8 -- a CPU test read
# at half its time, so one grown past the line would read green (GOH.LONGPOLELOAD). The probe is
# the WALL of the speed probe's fixed loop, run now on this test's own thread, over what the loop
# costs a quiet core of this box: the run queue, the QoS clamp, and an efficiency core all slow
# it as they slowed the phase. Taken at a phase's end, and only after one of POLE_PROBE_AFTER_S or
# more: a shorter phase is its wall (no load makes three of them a long pole), and the probe's
# ~65 ms would be most of it.
#
# UNMEASURED UNDER LOAD (GOH.LONGPOLEUNMEASURED). Every phase also carries its CPU seconds and
# whether the run queue over it (`run_queue`) passed lib/long_pole.py QUIET_LOAD; the controller
# keeps a loaded test's entry as the last quiet run read it, unless its CPU outgrew that run's
# (`settle`): one stretch sample cannot correct a wall the load moved across the whole test.
# A phase also carries its run queue (`goh_pole_load`, written to the reading's `load`) and whether
# it missed a kept store (`goh_pole_cold`, `kept_misses`): a cold store's CPU is the store
# refilling, never the test's growth (GOH.LONGPOLECOLD).
_CPU_MARK = pytest.StashKey[float]()
_MISS_MARK = pytest.StashKey[int]()


def kept_misses() -> int:
    """Keys this process's kept stores missed so far (tests/_kept_store.py `missed`), 0 when none
    was loaded: a phase that missed one read COLD (GOH.LONGPOLECOLD), its CPU the store refilling,
    which lib/long_pole.py `settle` never takes for the test's growth."""
    kept = sys.modules.get("_kept_store")
    return int(kept.missed()) if kept is not None and hasattr(kept, "missed") else 0


POLE_PROBE_AFTER_S = 2.0
POLE_PROBE_SAMPLES = 5


def stretch_of(walls: list[float]) -> float:
    """The stretch the walls of the probe's loops read: their MEDIAN over what one loop costs a
    quiet core. One ~65 ms sample read 25% low and an outlier moved it either way under load
    (GOH.LONGPOLEFLAKE); a median of several short ones is held by the middle of them."""
    ordered = sorted(walls)
    mid = (
        ordered[len(ordered) // 2]
        if len(ordered) % 2
        else sum(ordered[len(ordered) // 2 - 1 :][:2]) / 2
    )
    return max(1.0, mid / (REFERENCE_CPU_S * speed_factor()))


def pole_stretch() -> float:
    """How many times longer than on a quiet core of this box this thread runs CPU work now."""
    walls = []
    for _ in range(POLE_PROBE_SAMPLES):
        t0 = time.perf_counter()
        _loop()
        walls.append(time.perf_counter() - t0)
    return stretch_of(walls)


def run_queue(start: float, stop: float) -> float:
    """Runnable threads per core over a phase (`load_factor` without the box's speed): the figure
    a long-pole reading is judged quiet or loaded by (lib/long_pole.py QUIET_LOAD)."""
    return load_factor(start, stop) / speed_factor()


def _cpu_s() -> float:
    """CPU seconds of this process and every child it has waited for."""
    own = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + kids.ru_utime + kids.ru_stime


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Start the load sampler before the first fixture: a setup is judged by the load it ran in."""
    item.stash[_CPU_MARK] = _cpu_s()
    item.stash[_MISS_MARK] = kept_misses()
    _start_sampler()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    _start_sampler()
    outcome = yield
    rep = outcome.get_result()
    now = _cpu_s()
    cpu = now - item.stash.get(_CPU_MARK, now)
    item.stash[_CPU_MARK] = now
    missed = kept_misses()
    rep.goh_pole_cold = missed > item.stash.get(_MISS_MARK, missed)
    item.stash[_MISS_MARK] = missed
    stretch = pole_stretch() if call.duration >= POLE_PROBE_AFTER_S else 1.0
    rep.goh_pole = round(pole(call.duration, cpu, stretch), 3)
    rep.goh_pole_cpu = round(max(0.0, cpu), 3)
    queue = run_queue(call.start, call.stop)
    rep.goh_pole_load = round(queue, 3)
    rep.goh_pole_loaded = queue > QUIET_LOAD
    if rep.when not in ("setup", "call") or not rep.passed:
        return
    setup = rep.when == "setup"
    ceiling = setup_ceiling(item.nodeid) if setup else SLOW.get(item.nodeid, CEILING_S)
    factor = load_factor(call.start, call.stop)
    if call.duration > ceiling * factor:
        stretch = f" x{factor:.2g} for the box's load and speed" if factor > 1 else ""
        rep.outcome = "failed"
        if setup:
            rep.longrepr = (
                f"{item.nodeid} setup took {call.duration:.1f} s, over its {ceiling:g} s ceiling"
                f"{stretch} (tests/_drift_guard.py): the fixtures this test was the first to need, "
                "a module or session fixture charged once. Make the fixture cheaper, or name its "
                "test FILE in SETUP_SLOW with a reason."
            )
            return
        rep.longrepr = (
            f"{item.nodeid} took {call.duration:.1f} s, over its {ceiling:g} s ceiling{stretch} "
            "(tests/_drift_guard.py): a leaked child holding a captured pipe, a hidden build, or a "
            "test that runs a whole gate to check one step. Fix it, or name it in SLOW with a reason."
        )


def prebuild_live(config, root: Path | None = None) -> None:
    """The goh every gate a test spawns will resolve -- built once, here, in the controller.

    Under GOH_LIVE with the working tree's Rust ahead of HEAD that is the tree's build
    (`goh_live_binary`); otherwise HEAD's (bin/goh, or the store). Only the first half used to be
    done here, so a checkout whose bin/goh was not HEAD's build -- every fresh worktree, every commit
    whose crates moved -- left the FIRST gate a test ran to build it, which the cargo shim refuses:
    9 tests of test_rust_gate.py failed in any new worktree (G2c). `goh_resolve_native` is the one
    resolver the gates call, so the controller asks exactly what they will.
    """
    if hasattr(config, "workerinput") or not os.environ.get("GOH_LIVE"):
        return  # a worker: the controller already did it
    if os.environ.get("GOH_STAGED_GUARD_RUN"):
        # A commit's source guard (gates/staged_guards.sh) runs tests that read the tree's text and
        # spawn no gate; inside a fixture's commit there is not even a cargo to build with.
        return
    r = subprocess.run(
        ["bash", "-c", ('source "$1/gates/_goh_bin.sh"; goh_resolve_native; [ -n "$goh_native" ] '
         '|| { echo "$goh_native_why" >&2; false; }'), "_", str(root or _ROOT)],
        capture_output=True, text=True, timeout=900, check=False,
        env=dict(os.environ, DRIFT_BUILD_OK="1"), stdin=subprocess.DEVNULL,
    )  # fmt: skip
    if r.returncode != 0:
        raise pytest.UsageError(f"the goh the gates resolve did not build:\n{r.stderr}")
