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
from pathlib import Path

REAL_HOME = os.path.realpath(os.path.expanduser("~"))
MARKER = "GATES_OF_HECK_SUITE_REAL_HOME"  # not GOH_-prefixed: no env scrub drops it

READ = ("Projects", ".claude/skills", ".cargo")
# The GOH_* variables a hermetic child keeps: locations and test seams, never an opt-in.
# GOH_MEM_ADMIT: host_locks.py turns memory admission off for every fixture gate, and a scrub that
# dropped it sent each hermetic land (tools/load.py 0, admitted by lib/gate_slot.py) to the host's
# memory reading, a wait of up to 600 s on a swapping machine (GOH.ONEADMISSION).
KEPT_GOH = ("GOH_LIVE", "GOH_ESTATE_STATE", "GOH_GRACE", "GOH_MEM_ADMIT")
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
    # gates/staged_guards.sh marks its run of the staged guards (tests/_drift_guard.py).
    "GOH_STAGED_GUARD_RUN",
    # tools/land.sh's pre-step and gate hand the suite its verdict record (tests/_land_verdicts.py,
    # GOH.LANDPREDUP): dropped here, as they were until GOH.VERDICTCARRY, no land reused a verdict.
    "GOH_LAND_VERDICTS_RECORD",
    "GOH_LAND_VERDICTS_REUSE",
    # ... and the CALLER's verdict store, resolved before this home replaces HOME (`_land_store`).
    "GOH_LAND_VERDICTS_STORE",
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
            (home / rel).symlink_to(real / rel)
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


def _enter() -> None:
    _drop_ship_inputs()
    # token-pool's state dir holds the Claude meter agent_guard's budget guard reads
    # (GOH.ULTRARULES): a caller's would decide every guard test's profile. The suite reads none.
    os.environ.pop("POOL_STATE_DIR", None)
    if os.environ.get(
        MARKER
    ):  # a nested run (an xdist worker, a suite a test starts) keeps its own
        return
    _land_store()
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
    furnish(home, READ)
    os.environ[MARKER] = REAL_HOME
    # The user's XDG_STATE_HOME, kept by name so real_state_guard.py can watch it; the suite's own
    # state (goh's estate registry, the compact-nudge state) lives in the suite home instead.
    if os.environ.get("XDG_STATE_HOME"):
        os.environ["GATES_OF_HECK_SUITE_REAL_XDG_STATE_HOME"] = os.environ["XDG_STATE_HOME"]
    os.environ["HOME"] = str(home)
    os.environ["XDG_CACHE_HOME"] = str(home / ".cache")
    os.environ["XDG_STATE_HOME"] = str(home / ".local" / "state")
    # Keyless fixtures: new shapes and undeclared floors are advisory here (GOH.FLOORREQ).
    os.environ["GOH_GRACE"] = "within"


_enter()
import suite_tmp  # noqa: F401  (and the suite's own TMPDIR: tests/suite_tmp.py)
