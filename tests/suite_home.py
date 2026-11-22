"""The suite runs in its OWN home (imported first by conftest.py, before anything reads HOME).

THE CLASS (G11, G12). Every gate cache defaults to `${XDG_CACHE_HOME:-$HOME/.cache}/goh`: the push
logs and worktrees, the head export cache, the shared goh binary store, agent_guard's spawn ledger.
A test that scrubbed the one variable moving its writer fell back to the USER's cache -- a zero-byte
push log at every suite run, and a ledger row would corrupt the model-choice audit. So HOME and
XDG_CACHE_HOME point at a suite-owned home for the whole session, and a new writer is isolated
without anyone remembering it. lib/suite_cache_guard.sh refuses, under this suite, any write that
still lands in the real ~/.cache/goh.

The home lives under the REAL ~/.cache/goh/suite, not the temp directory: a push worktree must be
physical and outside $TMPDIR (SwiftLint's baseline; tests/test_push_gate.py). One per process,
removed at exit.

What the real home still provides is named here, explicitly: the toolchains (CARGO_HOME,
RUSTUP_HOME), the user site packages pytest-xdist lives in (PYTHONUSERBASE), the compile and package
caches (SCCACHE_DIR, UV_CACHE_DIR -- build caches, not goh's), and a MINIMAL git identity. And the
things the gates READ from the user's home by design, linked in by name: the estate the calibration
and corpus checks survey (~/Projects), the skills the structural gate reads (~/.claude/skills), and
cargo's shared advisory database (~/.cargo). Nothing else of the real home is reachable.

A KEPT VARIABLE'S DEFAULT IS THE SAME PLACE (HOME_DEFAULTS). A test that hands its child only `PATH`
and `HOME` drops RUSTUP_HOME and SCCACHE_DIR with everything else, and rustup and sccache fall back
to their defaults under HOME -- this throwaway home. There rustup found no toolchain and INSTALLED
stable into it on every run: a download and a brand-new compiler for sccache to hash inside a timed
build, which turned GOH.EA2's first land red (sccache exit 254, "could not parse/generate dep
info"; test_goh_build_publish.py). So each kept variable's default path under HOME links to what
the variable names, and a scrubbed child reaches the same toolchain and caches as an unscrubbed one.
"""

from __future__ import annotations

import atexit
import contextlib
import fcntl
import json
import os
import shutil
import site
import tempfile
from collections.abc import MutableMapping
from pathlib import Path

import _schedule  # (no HOME/TMPDIR read at import)
import ci_shape  # (no HOME/TMPDIR read at import)

REAL_HOME = os.path.realpath(os.path.expanduser("~"))
MARKER = "GATES_OF_HECK_SUITE_REAL_HOME"  # not GOH_-prefixed: no env scrub drops it

READ = ("Projects", ".claude/skills", ".cargo")
# What a READ link must NOT reach, by its entry's name (GOH.CIRED01310): the loop's checkout
# (`.loop` in the real Projects) is the owner's LIVE queue and the loopctl that writes it. A
# hosted runner has no copy, so a reader defaulting to it (lib/reuse_fault.py current_phase)
# passed on the box, on the owner's phase table, and v0.139.10's CI read "no such file"; and a
# default loopctl a test reached would have filed into the live queue. So `Projects` is a
# directory of links, one per entry, with none for these. A test that means the real loop names
# it through MARKER, as the real home's.
HIDDEN = {"Projects": frozenset({".loop"})}
# The GOH_* variables a hermetic child keeps: locations and test seams, never an opt-in.
# GOH_MEM_ADMIT: host_locks.py turns memory admission off for every fixture gate, and a scrub that
# dropped it sent each hermetic land (tools/load.py 0, admitted by lib/gate_slot.py) to the host's
# memory reading, a wait of up to 600 s on a swapping machine (GOH.ONEADMISSION).
# GOH_COV_GOH: the coverage run's instrumented goh (gates/_goh_bin.sh, GOH.COVGOHBIN), a binary
# taken only for the source it was built from; dropped, a hermetic gate run left no profile.
KEPT_GOH = ("GOH_LIVE", "GOH_ESTATE_STATE", "GOH_GRACE", "GOH_MEM_ADMIT", "GOH_COV_GOH")
# THE MACHINE'S SHAPE, one value on every box (GOH.CIRED0136): a fixture gate sized its gate
# slots (lib/gate_slot.py default_slots: cores // 8) and its worker share (lib/worker_share.py
# cores) from the box it ran on. The owner's 16 cores gave 2 slots and a share of 16; the 3-core
# GitHub runner 1 and 3, so v0.136.0's CI went red where the local gate was green: a 4-binary
# coverage run went "3 at a time", and with one slot a batch's speculative second half took the
# slot before the first (tests/test_landq_bisect_par.py). A test that means another shape says so.
MACHINE_SHAPE = {"GOH_GATE_CORES": "16", "GOH_GATE_SLOTS": "2"}
# THE CLOCK, one day in every child (GOH.MIDNIGHTDATE): a test expects PINNED_TODAY, never a date it
# reads from the clock. A gate crossing midnight split the day a test expected from the day the code
# (tools/release-kit/changelog_cut.py, via GOH_TODAY) wrote: four reds on 2026-11-19.
PINNED_TODAY = "2026-03-04"
CLOCK = {"GOH_TODAY": PINNED_TODAY}
# THE CLAMP OFF (GOH.LANDSHARE): a fixture's `load.py 0` or gate takes class `work` and would exec
# under the lane QoS clamp (lib/land_share.py) -- inside a LAND's suite too, slowing the land path's
# own tests 6-7x under contention. The suite runs at its gate's QoS, inherited; a test of the clamp
# turns it on by name.
QOS = {"GOH_LANE_QOS": "off"}
# THE REMOVABLE CACHE TIER OUT OF REACH (GOH.CACHEEVICT): a gate a test runs asks lib/cache_tier.py
# for the large tier before cargo; pointed nowhere, no test reads or writes a real /Volumes drive.
CACHE_TIER = {"GOH_CACHE_VOLUMES": "/nonexistent/goh-suite-volumes"}
HERMETIC_PINS = {
    **MACHINE_SHAPE,
    **CACHE_TIER,
    **CLOCK,
    **QOS,
}  # what every hermetic child is told, beside its scrub
# Where each kept variable's tool looks under HOME when the variable is unset.
HOME_DEFAULTS = {
    "CARGO_HOME": ".cargo",
    "RUSTUP_HOME": ".rustup",
    "SCCACHE_DIR": "Library/Caches/Mozilla.sccache",
    "UV_CACHE_DIR": ".cache/uv",
}

GITCONFIG = "[user]\n\tname = goh suite\n\temail = suite@goh.test\n[init]\n\tdefaultBranch = main\n"


# A ship's run inputs (tools/ship.py exports them to its release step, whose gate runs this suite)
# are the CALLER's, never a test's. GOH.SHIPSUMMARY made release.sh title its cut with
# GOH_SHIP_SUMMARY, and v0.97.0's own release gate went red: four fixture kits cut their CHANGELOG
# under the real release's summary (test_changelog_cut, test_release_kit_version). Dropped at every
# entry, a nested one too.
SHIP_PREFIX = "GOH_SHIP_"

# EVERY GOH_* the caller set is the caller's (GOH.LIVEROOTLEAK). The same ship's export leaked
# GOH_LIVE_ROOT into v0.119.0's release suite and every gate a test ran there resolved main's goh;
# a push gate's GOH_CHANGE_BASE turned a red arm green (GOH.RUNNERPIN); a parent gate's
# GOH_RESOLVED_BIN answers for a binary this tree did not build. Dropping by name contained one
# variable at a time, so a fresh entry drops them ALL and keeps only these, the gate's declared
# inputs to this suite:
CALLER_INPUTS = (
    # tools/pytest.sh, this suite's own runner, resolved the house interpreter this pytest IS
    # (lib/house_python.sh); kept, every child agrees with it instead of searching again.
    "GOH_PY",
    "GOH_PY_OK",
    # The coverage gate's external pass (GOH_COV_RUST_EXTERNAL) hands the suite its INSTRUMENTED
    # goh (conftest.native_goh); dropped, the suite builds its own and the crate's coverage loses
    # the suite's share.
    "GOH_TEST_BIN",
    "GOH_COVERAGE_BIN_DIR",
    # ... and the same goh for the gate scripts a test runs (gates/_goh_bin.sh, GOH.COVGOHBIN):
    # dropped here, their runs stayed the release build's and left no profile.
    "GOH_COV_GOH",
    # gates/staged_guards.sh marks its run of the staged guards (tests/_drift_guard.py).
    "GOH_STAGED_GUARD_RUN",
    # tools/land.sh's pre-step and gate hand the suite its verdict record (tests/_land_verdicts.py,
    # GOH.LANDPREDUP): dropped here, as they were until GOH.VERDICTCARRY, no land reused a verdict.
    "GOH_LAND_VERDICTS_RECORD",
    "GOH_LAND_VERDICTS_REUSE",
    # ... and the CALLER's verdict store, resolved before this home replaces HOME (`_land_store`).
    "GOH_LAND_VERDICTS_STORE",
    # The ship's file for its reused and fresh binary tests (tests/_prof_reuse.py): dropped here,
    # no ship record named them (GOH.COVPROFILEREUSE's first measured run).
    "GOH_PROF_REUSE_OUT",
)
# What the suite sets for itself, after the drop: GOH_DIR (below, its own checkout -- unset, the
# binary judged the default checkout's checkers, D.GOH1), GOH_GRACE (below), GOH_LIVE
# (conftest), GOH_ESTATE_STATE (conftest's session fixture) and the private lock and cache roots
# (host_locks.py, per test).
SUITE_ROOT = str(Path(__file__).resolve().parent.parent)


def _land_store() -> None:
    """A land's suite records into the caller's store (lib/reuse.py root), not this home's."""
    if not (
        os.environ.get("GOH_LAND_VERDICTS_RECORD") or os.environ.get("GOH_LAND_VERDICTS_REUSE")
    ):
        return
    named, state = os.environ.get("GOH_REUSE_DIR", ""), os.environ.get("XDG_STATE_HOME", "")
    base = Path(state) if state.startswith("/") else Path(REAL_HOME) / ".local" / "state"
    store = named if named.startswith("/") else str(base / "goh" / "reuse")
    os.environ.setdefault("GOH_LAND_VERDICTS_STORE", store)


def _drop_ship_inputs() -> None:
    for name in [k for k in os.environ if k.startswith(SHIP_PREFIX)]:
        del os.environ[name]


# The suite's LIVE SHARE (GOH.SHAREGROW): tools/pytest.sh's token pool and what the count reads
# (GOH_SHARE_*, lib/worker_share.py) are the RUNNER's, not any test's. Packed under a name no scrub
# drops, so the xdist workers this process starts find them (tests/_share_tokens.py unpacks and
# removes them in each worker); every test's own environment still has no GOH_SHARE_*.
SHARE_PACKED = "GATES_OF_HECK_SUITE_SHARE"


def _pack_share() -> None:
    share = {k: v for k, v in os.environ.items() if k.startswith("GOH_SHARE_")}
    if share.get("GOH_SHARE_POOL"):
        os.environ[SHARE_PACKED] = json.dumps(share)


def _drop_caller_goh() -> None:
    _pack_share()
    for name in [k for k in os.environ if k.startswith("GOH_") and k not in CALLER_INPUTS]:
        del os.environ[name]
    os.environ["GOH_DIR"] = SUITE_ROOT
    # The import path is a LOCATION too (GOH.ORPHANCANARYENV): a child a test spawned found goh's
    # `tui` only through the caller's PYTHONPATH, so `env -u PYTHONPATH` failed what the land
    # suites passed, and a caller's naming main had a worktree's children import main's modules.
    os.environ["PYTHONPATH"] = SUITE_ROOT


def furnish(home: Path, reads: tuple[str, ...]) -> Path:
    """`home` made a home: the minimal git identity, a cache, `reads` linked from the REAL home and
    each kept variable's default path. `reads=()` is a home with NONE of the machine's own state,
    which a hosted runner has no copy of (tests/_self_walk.py, GOH.SELFWALKCI)."""
    real = Path(os.environ.get(MARKER) or REAL_HOME)
    home.mkdir(parents=True, exist_ok=True)
    (home / ".gitconfig").write_text(GITCONFIG)
    (home / ".cache").mkdir(exist_ok=True)
    for rel in reads:
        if (real / rel).exists():
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            _link(real / rel, home / rel, HIDDEN.get(rel, frozenset()))
    # A kept place not made yet is made, then linked: a fresh hosted runner has no sccache cache
    # though the gate pins SCCACHE_DIR to it, and an unlinked default was a second, empty cache
    # (GOH.CIRUSTGATE, advisory CI run 43624157558). The tool would make the same directory.
    for name, rel in HOME_DEFAULTS.items():
        kept = os.environ.get(name)
        if kept and not os.path.lexists(home / rel):
            Path(kept).mkdir(parents=True, exist_ok=True)
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            (home / rel).symlink_to(kept)
    return home


def _link(src: Path, dst: Path, hidden: frozenset[str]) -> None:
    """`dst` reaching `src`: one link, or, when some entries are `hidden`, a directory of links to
    every other entry, so no path below `dst` reaches a hidden one. A `src` holding none of them
    (a nested session's, whose real home is a suite home already furnished) is one link too: its
    ~45 links per nested home were most of the suite home's file events (GOH.FSCHURN3)."""
    if not hidden or not src.is_dir() or not any(os.path.lexists(src / h) for h in hidden):
        dst.symlink_to(src)
        return
    dst.mkdir(exist_ok=True)
    for entry in src.iterdir():
        if entry.name not in hidden:
            (dst / entry.name).symlink_to(entry)


# The slot locks this process holds for its whole life (GOH.FSCHURN): released by the kernel when
# it exits, however it exits. Held here so no garbage collection closes one.
_HELD: list[int] = []
SLOTS = 256


def _writable_rmtree(path: Path) -> None:
    """Remove `path`, a tree a killed run left, read-only parts included."""

    def again(func, name, _exc) -> None:
        os.chmod(os.path.dirname(name), 0o700)
        with contextlib.suppress(FileNotFoundError):
            os.chmod(name, 0o700)
        func(name)

    shutil.rmtree(path, onexc=again)


def claim_home(parent: Path) -> Path:
    """A home of this process's own under `parent` at a REUSED name (GOH.FSCHURN, 2026-11-18).

    A fresh `home.XXXXXXXX` for every suite run (its xdist workers share it) made every path the
    suite wrote below it -- push worktrees, caches, state -- a path fseventsd had never seen, and
    fseventsd sat at one core and 11.7 GB with goh 78-99% of the file events on /. So a home is a
    SLOT, `home.slotNN`, the lowest one whose lock (`home.slotNN.lock`, an flock the kernel drops
    when this process ends, however it ends) is free. A home a killed process left behind is emptied
    before its slot is handed out again; past the last slot, a fresh name as before."""
    parent.mkdir(parents=True, exist_ok=True)
    for n in range(SLOTS):
        fd = os.open(parent / f"home.slot{n:02d}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            continue
        _HELD.append(fd)
        home = parent / f"home.slot{n:02d}"
        if os.path.lexists(home):  # a killed process's: its lock was free, so no one holds it
            if home.is_dir() and not home.is_symlink():
                _writable_rmtree(home)
            else:
                home.unlink()
        home.mkdir()
        return home.resolve()
    return Path(tempfile.mkdtemp(prefix="home.", dir=parent)).resolve()


# THE CI SHAPE (GOH.CIPARITY, tests/ci_shape.py): a LAND's suite runs as hosted CI runs it, so a
# test green only on this box is red before it lands, not after its release. A land's suite is the
# one that carries a land key (tools/land.sh hands GOH_LAND_VERDICTS_RECORD to its pre-step and
# GOH_LAND_VERDICTS_REUSE to its gate, and refuses a green pre-step whose record never came back),
# so no land runs another shape; SHAPE=ci asks for it by hand. In it the home links NOTHING of the
# owner's (no ~/Projects, no ~/.claude/skills: a runner has neither), every XDG directory is inside
# it, and gate.yml's variables are set before the caller's GOH_* drop, so what reaches a test is
# what reaches it on the runner. Not GOH_-prefixed: no env scrub drops it.
SHAPE = "GATES_OF_HECK_SUITE_SHAPE"
SHAPES = ("box", "ci")
LAND_KEYS = ("GOH_LAND_VERDICTS_RECORD", "GOH_LAND_VERDICTS_REUSE")


def shape(env: dict[str, str] | None = None) -> str:
    """`ci` for a land's suite or one that names it, `box` otherwise; another name is refused."""
    env = os.environ if env is None else env
    named = env.get(SHAPE, "")
    if named and named not in SHAPES:
        raise SystemExit(f"tests/suite_home.py: {SHAPE}={named!r} is none of {', '.join(SHAPES)}")
    return "ci" if named == "ci" or any(env.get(k) for k in LAND_KEYS) else "box"


def _shape_ci() -> None:
    """gate.yml's gate-step variables (tests/ci_shape.py overlay), and the shape named for every
    child, so a fixture's nested entry and this run agree."""
    applied = ci_shape.overlay(Path(SUITE_ROOT) / ci_shape.WORKFLOW, SUITE_ROOT)
    os.environ.update(applied)
    os.environ[ci_shape.APPLIED] = json.dumps(sorted(applied))
    os.environ[SHAPE] = "ci"


# The caller's shell startup files (GOH.ZSHSPLITGUARD, 2026-11-21): `BASH_ENV` (and POSIX `ENV`)
# make EVERY non-interactive bash child source the named rc. The agent tool shell's is ~/.bashrc,
# which rebuilds its env from a `zsh -lic` under $HOME -- this suite's throwaway home -- and exports
# that PATH over the one the test gave its child: the gate's own `cargo` went missing and the whole
# suite refused to start ("cargo: command not found", tests/_drift_guard.py prebuild_live). No test
# child sources the owner's rc; a test that wants one passes it.
SHELL_RC_VARS = ("BASH_ENV", "ENV")


def drop_shell_rc(environ: MutableMapping[str, str]) -> None:
    for name in SHELL_RC_VARS:
        environ.pop(name, None)


def _enter() -> None:
    drop_shell_rc(os.environ)
    # The duration table tools/pytest.sh names for its top-level run (GOH.DURATIONSSTALE), POPPED
    # before anything here starts a child: the run it was named for owns it, and no xdist worker or
    # fixture run under it inherits it to write it.
    _schedule.take()
    _drop_ship_inputs()
    # token-pool's state dir holds the Claude meter agent_guard's budget guard reads
    # (GOH.ULTRARULES): a caller's would decide every guard test's profile. The suite reads none.
    os.environ.pop("POOL_STATE_DIR", None)
    if os.environ.get(
        MARKER
    ):  # a nested run (an xdist worker, a suite a test starts) keeps its own
        return
    _land_store()
    ci = shape() == "ci"
    if ci:
        _shape_ci()
    _drop_caller_goh()
    real = Path(REAL_HOME)
    keep = {
        "CARGO_HOME": real / ".cargo",
        "RUSTUP_HOME": real / ".rustup",
        "PYTHONUSERBASE": Path(site.getuserbase()),
        "SCCACHE_DIR": real / "Library" / "Caches" / "Mozilla.sccache",
        "UV_CACHE_DIR": real / ".cache" / "uv",
    }
    for name, path in keep.items():
        if name not in os.environ and path.exists():
            os.environ[name] = str(path)
    parent = real / ".cache" / "goh" / "suite"
    parent.mkdir(parents=True, exist_ok=True)
    home = claim_home(parent)
    atexit.register(shutil.rmtree, home, True)
    furnish(home, () if ci else READ)
    os.environ[MARKER] = REAL_HOME
    # The user's XDG_STATE_HOME, kept by name so real_state_guard.py can watch it; the suite's own
    # state (goh's estate registry, the compact-nudge state) lives in the suite home instead.
    if os.environ.get("XDG_STATE_HOME"):
        os.environ["GATES_OF_HECK_SUITE_REAL_XDG_STATE_HOME"] = os.environ["XDG_STATE_HOME"]
    os.environ["HOME"] = str(home)
    os.environ["XDG_CACHE_HOME"] = str(home / ".cache")
    os.environ["XDG_STATE_HOME"] = str(home / ".local" / "state")
    if ci:  # a runner's XDG directories are its home's too; the caller's never reach a test
        os.environ["XDG_CONFIG_HOME"] = str(home / ".config")
        os.environ["XDG_DATA_HOME"] = str(home / ".local" / "share")
    # Keyless fixtures: new shapes and undeclared floors are advisory here (GOH.FLOORREQ).
    os.environ["GOH_GRACE"] = "within"


_enter()
import suite_tmp  # noqa: F401  (and the suite's own TMPDIR: tests/suite_tmp.py)
