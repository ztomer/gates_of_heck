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
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

try:
    from lib.tmp_root import goh_tmp
except ImportError:  # lib/ itself on the path
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
def owned() -> Iterator[Path]:
    """The run's directory: the inherited one (removed by its owner), else a new one, removed when
    the block ends. The caller hands it to its children as ENV."""
    named = os.environ.get(ENV, "")
    if named and os.path.isdir(named):
        yield Path(named)
        return
    made = Path(tempfile.mkdtemp(prefix=PREFIX, dir=goh_tmp()))
    try:
        yield made
    finally:
        shutil.rmtree(made, ignore_errors=True)


@contextlib.contextmanager
def in_environ() -> Iterator[Path]:
    """`owned()`, named in this process's environment for the block: a caller whose children take
    os.environ (tools/affected_tests.py's pytest runs)."""
    prior = os.environ.get(ENV)
    with owned() as run:
        os.environ[ENV] = str(run)
        try:
            yield run
        finally:
            if prior is None:
                os.environ.pop(ENV, None)
            else:
                os.environ[ENV] = prior


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
        first = marker.read_text(encoding="utf-8", errors="replace").strip() or "an unnamed caller"
        raise BuiltTwice(
            f"{kind} ({key}) is built once per run ({run}): {first} built it; {caller} asked to "
            "build it again -- take the run's copy (lib/build_once.py, GOH.BUILDONCE)"
        ) from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"{caller} (pid {os.getpid()})\n")


def builds(run: Path) -> dict[str, int]:
    """{kind: builds} claimed in RUN so far: the reading an owner records."""
    out: dict[str, int] = {}
    with contextlib.suppress(FileNotFoundError):
        for m in (run / "builds").iterdir():
            kind = m.name.rpartition(".")[0]
            out[kind] = out.get(kind, 0) + 1
    return out
