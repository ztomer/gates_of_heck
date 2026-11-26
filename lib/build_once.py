"""lib/build_once.py -- one owner per run for what a run builds (GOH.BUILDONCE).

WHY. `goh land --check` runs its guard tests as eight pytest runs side by side, and the land
pre-step as two (the judges, then the rest). Each run built its own goh test binary
(tests/conftest.py `native_goh`: a `cargo build -p goh` and a copy), so one check built it eight
times (read 2026-11-25 from a traced check: 8 builds in 8 processes). "Once per session" was true
of each pytest run and false of the run the owner meant: the whole check.

THE OWNER is the outermost caller of the run (lib/land_check.py `run`, tools/affected_tests.py
`--run`): `owned()` makes the run's directory and names it in GOH_BUILD_RUN for every child, which
tests/suite_home.py keeps (a CALLER_INPUT); a caller already inside a run reuses it and removes
nothing. A builder publishes into `run_dir(...)`, so the first pytest run's binary is every later
one's.

THE REFUSAL. A builder calls `claim(run, kind, key, caller)` right before it builds: the first
claim of (kind, key) in a run is the build, a second is `BuiltTwice`, naming the first builder and
the caller asking again -- never a quiet rebuild. One empty marker file per build, created
exclusively (O_EXCL), so two processes racing cannot both win. Pinned by tests/test_build_once.py.

WHAT IS ONCE (GOH.BUILDONCE part 2). The run names its TREE (`owned(tree)`, the file `tree` in the
run's directory): the checkout the owner judges, fixed for the run. `shared(kind, root, build)` is
the dependency map's door (lib/test_closure.py `Closure`): for the run's tree, one build per run,
kept in the run as a pickle every later process loads; any other root (a test's fixture tree) is
built as before. The templates (tests/_tree_clone.py) claim theirs the same way.

THE READING. Each build's marker holds its caller and seconds; the owner writes them as goh.db rows
(`run_builds`, migration 0027) when the run ends (`record`), one per build, never for a reuse.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import pickle
import shutil
import sqlite3
import sys
import tempfile
import time
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path

try:
    from lib import lock_order
    from lib.tmp_root import goh_tmp
except ImportError:  # lib/ itself on the path
    import lock_order  # type: ignore[no-redef]
    from tmp_root import goh_tmp  # type: ignore[no-redef]

ENV = "GOH_BUILD_RUN"  # the run's directory, for every child of the owner
PREFIX = os.environ.get("GOH_BUILD_RUN_PREFIX", "goh-build-run.")  # under the owner's TMPDIR


class BuiltTwice(RuntimeError):
    """A second build of one (kind, key) inside one run."""


def run_dir(default: Path) -> Path:
    """The run's directory when an owner named one (ENV), else DEFAULT (a lone pytest run's)."""
    named = os.environ.get(ENV, "")
    return Path(named) if named and os.path.isdir(named) else Path(default)


@contextlib.contextmanager
def owned(tree: Path | None = None) -> Iterator[Path]:
    """The run's directory: the inherited one (removed by its owner), else a new one naming TREE,
    its builds written as rows and the directory removed when the block ends. The caller hands it
    to its children as ENV."""
    named = os.environ.get(ENV, "")
    if named and os.path.isdir(named):
        yield Path(named)
        return
    made = Path(tempfile.mkdtemp(prefix=PREFIX, dir=goh_tmp()))
    if tree is not None:
        (made / "tree").write_text(os.path.realpath(tree), encoding="utf-8")
    try:
        yield made
    finally:
        record(made)
        for here, _dirs, _files in os.walk(made):  # a sealed template's read-only git dirs
            if not os.access(here, os.W_OK):
                with contextlib.suppress(OSError):
                    os.chmod(here, 0o755)
        shutil.rmtree(made, ignore_errors=True)


@contextlib.contextmanager
def in_environ(tree: Path | None = None) -> Iterator[Path]:
    """`owned(tree)`, named in this process's environment for the block: a caller whose children
    take os.environ (tools/affected_tests.py's pytest runs, lib/land_check.py's jobs)."""
    prior = os.environ.get(ENV)
    with owned(tree) as run:
        os.environ[ENV] = str(run)
        try:
            yield run
        finally:
            if prior is None:
                os.environ.pop(ENV, None)
            else:
                os.environ[ENV] = prior


def current() -> Path | None:
    """The run this process is inside, if an owner named one."""
    named = os.environ.get(ENV, "")
    return Path(named) if named and os.path.isdir(named) else None


def run_of(path: Path) -> Path:
    """The run PATH lives in (an owner's), else PATH itself: where a build there claims."""
    run = current()
    return run if run and str(path).startswith(f"{run}/") else path


def home_for(default: Path, kind: str, root: Path) -> Path:
    """The current run's home for KIND of ROOT when ROOT is the run's tree, else DEFAULT."""
    run = current()
    if run is None or tree(run) != os.path.realpath(root):
        return default
    return home(run, kind, Path(os.path.realpath(root)))


def caller() -> str:
    """Who asked: the first frame outside this file that is no `__init__` or lambda."""
    f = sys._getframe(1)
    while f is not None and (f.f_code.co_filename == __file__ or f.f_code.co_name in SKIP):
        f = f.f_back
    return f"{os.path.relpath(f.f_code.co_filename)}:{f.f_code.co_name}" if f else "?"


SKIP = ("__init__", "<lambda>")


def for_argv(argv: list[str], flag: str = "--run"):
    """`in_environ` of ARGV's `--root` (else the cwd) when ARGV holds FLAG; else no run."""
    if flag not in argv:
        return contextlib.nullcontext()
    at = argv.index("--root") + 1 if "--root" in argv else 0
    return in_environ(Path(argv[at]) if 0 < at < len(argv) else Path.cwd())


def tree(run: Path) -> str:
    """The checkout RUN judges ('' when its owner named none)."""
    with contextlib.suppress(OSError):
        return (run / "tree").read_text(encoding="utf-8").strip()
    return ""


def home(run: Path, kind: str, root: Path) -> Path:
    """Where RUN keeps its KIND built from the tree at ROOT: one per tree, so a nested run of
    another checkout never takes this one's."""
    return run / kind / hashlib.sha256(str(root).encode()).hexdigest()[:12]


def _marker(run: Path, kind: str, key: str) -> Path:
    digest = hashlib.sha256(f"{kind}\0{key}".encode()).hexdigest()[:16]
    return run / "builds" / f"{kind.replace(' ', '-')}.{digest}"


def claim(run: Path, kind: str, key: str, caller: str) -> None:
    """Record that CALLER builds KIND (for KEY) in RUN, or refuse: it was built in this run."""
    marker = _marker(run, kind, key)
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        said = marker.read_text(encoding="utf-8", errors="replace").splitlines()
        first = said[0] if said else "an unnamed caller"
        raise BuiltTwice(
            f"{kind} ({key}) is built once per run ({run}): {first} built it; {caller} asked to "
            "build it again -- take the run's copy (lib/build_once.py, GOH.BUILDONCE)"
        ) from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"{caller} (pid {os.getpid()})\n")


@contextlib.contextmanager
def building(run: Path, kind: str, key: str, caller: str) -> Iterator[None]:
    """`claim`, then the build the block makes, its seconds kept in the marker for `record`."""
    claim(run, kind, key, caller)
    t0 = time.monotonic()
    yield
    with open(_marker(run, kind, key), "a", encoding="utf-8") as f:
        f.write(f"{time.monotonic() - t0:.3f}\n")


def template_build(parent: Path, key: str):
    """`building` of the template KEY under PARENT, claimed in the run PARENT is in, for the test
    that asked first (tests/_tree_clone.py; goh-templates and goh-process-templates apart)."""
    who = os.environ.get("PYTEST_CURRENT_TEST", "") or f"pid {os.getpid()}"
    return building(run_of(parent), "template", f"{parent.name}/{key}", who)


_SHARED: dict[tuple[str, str, str], object] = {}  # (run, kind, tree): this process's copy


def shared(kind: str, root: Path, build: Callable[[], object], who: str = "") -> object | None:
    """What BUILD makes of ROOT, built once per run when ROOT is the run's tree: this process's
    copy, else the run's pickle, else built here (claimed: a second build is BuiltTwice) and kept
    for the run. None outside a run or for another root: the caller builds as before."""
    run = current()
    if run is None or not (named := tree(run)) or os.path.realpath(root) != named:
        return None
    key = (str(run), kind, named)
    if key in _SHARED:
        return _SHARED[key]
    kept = home(run, kind.replace(" ", "-"), Path(named)) / "built.pickle.z"
    kept.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f"{kept}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        lock_order.flock(fd, "build_run")  # a blocking wait for the one builder, never a poll
        if kept.is_file():
            made = pickle.loads(zlib.decompress(kept.read_bytes()))
        else:
            with building(run, kind, named, who or caller()):
                made = build()
            staging = kept.with_suffix(f".{os.getpid()}")
            staging.write_bytes(zlib.compress(pickle.dumps(made, 5), 1))
            staging.rename(kept)
    finally:
        os.close(fd)
    _SHARED[key] = made
    return made


def builds(run: Path) -> list[tuple[str, str, float | None]]:
    """(kind, caller, seconds) of each build claimed in RUN so far: the reading `record` writes."""
    out = []
    with contextlib.suppress(FileNotFoundError):
        for m in sorted((run / "builds").iterdir()):
            lines = m.read_text(encoding="utf-8", errors="replace").splitlines()
            secs = float(lines[1]) if len(lines) > 1 else None
            out.append((m.name.rpartition(".")[0], lines[0] if lines else "", secs))
    return out


def record(run: Path) -> int:
    """RUN's builds as goh.db `run_builds` rows; how many were written (0, one stderr line, when
    goh.db refuses this copy's write or cannot be opened: a reading never fails its run)."""
    made = builds(run)
    if not made:
        return 0
    try:
        import goh_db  # lib/ on the path: the module a caller (or a test's seam) already holds
    except ImportError:
        from lib import goh_db  # type: ignore[no-redef]
    now = time.time()
    rows = [(run.name, kind, tree(run), caller, secs, now) for kind, caller, secs in made]
    try:
        with goh_db.refusable(), goh_db.transaction() as con:
            con.executemany(
                "INSERT INTO run_builds(run, kind, tree, caller, seconds, at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            return len(rows)
    except (OSError, sqlite3.Error, goh_db.Unknown) as e:
        print(f"goh.db: {len(rows)} run_builds rows not written ({e})", file=sys.stderr)
    return 0
