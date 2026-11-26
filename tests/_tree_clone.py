"""`clone_tree(src, dst)`: a private copy of a fixture template, in one syscall where it can be.

GOH.FIXTURECOST (2026-11-17): fixtures that build the same tree for every test (a committed copy of
gates/ checks/ lib/ tui/, a repository with a commit) build it ONCE per worker and clone it per
test.
On APFS, clonefile(2) copies a whole directory tree copy-on-write in one call (lib/: ~2 ms against
~65 ms for shutil.copytree, measured); elsewhere, or across volumes, it is shutil.copytree. Either
way the copy is the test's own: tests/test_tree_clone.py writes everything a test can into one clone
and reads the other and the template unchanged.
"""

from __future__ import annotations

import atexit
import contextlib
import ctypes
import errno
import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import _quiet_pack
import _tree_pool
from _reused_tmp import reused_mkdtemp
from _test_work import shared_setup
from timing_bounds import patience

# clonefile(2) cannot serve these: another volume, a filesystem without clones. Any other error is
# a real one and is raised.
_FALL_BACK = {errno.EXDEV, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS}


def _clonefile():
    if sys.platform != "darwin":
        return None
    fn = getattr(ctypes.CDLL(None, use_errno=True), "clonefile", None)
    if fn is not None:
        fn.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32)
        fn.restype = ctypes.c_int
    return fn


_CLONEFILE = _clonefile()


def copytree_clone(src: Path | str, dst: Path | str) -> None:
    """The portable copy: what `clone_tree` does where clonefile(2) is not available. A FILE is
    copied as one, as clonefile(2) does (GOH.WORKERFAILS: test_ship's template holds one)."""
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "clone target exists", str(dst))
    if not os.path.isdir(src) or os.path.islink(src):
        shutil.copy2(src, dst, follow_symlinks=False)
        return
    shutil.copytree(src, dst, symlinks=True)
    _unseal_clone(src, dst)


def clone_tree(src: Path | str, dst: Path | str) -> None:
    """Copy the tree `src` to `dst` (which must not exist): clonefile(2), else copytree."""
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "clone target exists", str(dst))
    sys.audit("goh.clone_tree", str(src))  # tests/_quiet_pack.py
    if _CLONEFILE is not None:
        here = _tree_pool.real_of(src)
        mine, its = _tree_pool.real_of(dst), _tree_pool.origin(here, _is_sealed)
        if its == here and _tree_pool.in_parts(here):
            os.mkdir(dst)
            for name in os.listdir(here):
                part = os.path.join(here, name)
                (_clone_part if os.path.islink(part) else clone_tree)(part, os.path.join(dst, name))
            shutil.copymode(here, dst)
            return
        if _tree_pool.take(its, mine, _clone_part, _is_sealed):
            return
        if _CLONEFILE(os.fsencode(its), os.fsencode(dst), 0) == 0:
            _unseal_clone(its, dst)
            _tree_pool.given(its, mine)
            return
        err = ctypes.get_errno()
        if err not in _FALL_BACK:
            raise OSError(err, os.strerror(err), str(dst))
    copytree_clone(src, dst)


_NOFOLLOW = 1  # CLONE_NOFOLLOW


def _clone_part(src: str, dst: str) -> None:
    if _CLONEFILE(os.fsencode(src), os.fsencode(dst), _NOFOLLOW) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), dst)


def _is_sealed(path: str) -> bool:
    """Whether the directory PATH of a template is one `_seal` made read-only."""
    return not os.access(path, os.W_OK)


# THE SHARED TEMPLATE IS SEALED (GOH.FSCHURN3). A run-wide template is every worker's, so a git
# that WRITES in it -- even `git status`, which refreshes the index under .git/index.lock -- is
# copied into whatever clone another worker takes meanwhile: GOH.LANDQWINFLAKE's clones carried the
# lock and their first `git add` died 128 (a842f3b6 fixed the one test that did it). Each
# repository's git dir, its objects/ and its refs/heads and refs/tags are made read-only once the
# template is built (`_seal`), so git itself refuses the write (an index, HEAD, config, FETCH_HEAD,
# a branch, a tag, an object: "Permission denied", exit 128) and an optional lock is simply not
# taken; `clone_tree` gives a clone its write bits back on those few directories only. Absolute
# template git dir -> the directories sealed in it, for `_unseal_clone`.
_SEALED: dict[str, tuple[str, ...]] = {}
_SEAL_PARTS = ("", "objects", "refs", "refs/heads", "refs/tags")


def _sealable(git_dir: Path) -> list[Path]:
    return [
        git_dir / part if part else git_dir for part in _SEAL_PARTS if (git_dir / part).is_dir()
    ]


def _seal(tree: Path) -> None:
    """Make every repository in `tree` refuse a write (see `_SEALED`), and remember where."""
    for git_dir in _repositories(tree):
        dirs = _sealable(git_dir)
        for d in reversed(dirs):  # the innermost first: the git dir itself last
            d.chmod(0o555)
        _SEALED[os.path.realpath(git_dir)] = tuple(os.path.relpath(d, git_dir) for d in dirs)


def _known_seals(tree: Path) -> None:
    """Register the seals of a template another worker built (the directories it sealed)."""
    for git_dir in _repositories(tree):
        key = os.path.realpath(git_dir)
        if key not in _SEALED:
            dirs = [d for d in _sealable(git_dir) if not os.access(d, os.W_OK)]
            if dirs:
                _SEALED[key] = tuple(os.path.relpath(d, git_dir) for d in dirs)


def _unseal_clone(src: Path | str, dst: Path | str) -> None:
    """The clone `dst` of `src` (a template, or a part of one) writable where the template is
    sealed: a chmod of each sealed directory the clone holds, never a walk of the clone."""
    src = _tree_pool.real_of(src)
    for git_dir, parts in _SEALED.items():
        if git_dir == src or git_dir.startswith(src.rstrip("/") + "/"):
            for part in parts:
                os.chmod(os.path.join(dst, os.path.relpath(git_dir, src), part), 0o755)


def unsealed_rmtree(tree: Path | str) -> None:
    """`shutil.rmtree` of a tree that may hold sealed repositories (this process's, or a killed
    one's stage): every directory's write bit back first."""
    tree = _tree_pool.real_of(tree)
    for git_dir in [g for g in _SEALED if g.startswith(tree.rstrip("/") + "/")]:
        del _SEALED[git_dir]
    for here, _dirs, _files in os.walk(tree):
        if not os.access(here, os.W_OK):
            with contextlib.suppress(OSError):
                os.chmod(here, 0o755)
    shutil.rmtree(tree, ignore_errors=True)


# id(session factory) -> (that factory, {key: the built template}). A fixture defined in a test
# module and IMPORTED by others (`from test_proven import goh`) cannot name a session fixture of its
# own without every importer importing that too; `from_template` needs only pytest's own
# session-scoped `tmp_path_factory`. The factory is held, so its id() cannot be reused by another.
_TEMPLATES: dict[int, tuple[object, dict[str, Path]]] = {}


def template_of(factory, key: str) -> Path | None:
    """The template `from_template` built for `key` in this session, if any."""
    return _TEMPLATES.get(id(factory), (None, {}))[1].get(key)


def from_template(factory, key: str, build, dst: Path) -> Path:
    """`dst` as a clone of the tree `build(path)` makes, built ONCE per run (`_run_template`) under
    `factory` (the session's `tmp_path_factory`). `build` must make the same tree every time: it is
    what each test would have built for itself, and nothing the test then writes reaches it."""
    clone_tree(template(factory, key, build), dst)
    return dst


def template(factory, key: str, build) -> Path:
    """The tree `build(path)` makes, built ONCE per run -- by the first worker to ask, shared by
    the others -- and never handed out to write in: a caller clones it, or parts of it, with
    `clone_tree`."""
    built = _TEMPLATES.setdefault(id(factory), (factory, {}))[1]
    if key not in built:
        built[key] = _run_template(_run_dir(factory) / "goh-templates", key, build)
        _known_seals(built[key])
    return built[key]


def _run_dir(factory) -> Path:
    """The directory every worker of this run shares: an xdist worker's basetemp is
    `<run>/popen-gwN`, a lone session's is the run's own."""
    base = factory.getbasetemp()
    return base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base


def _run_template(parent: Path, key: str, build, keep_quiet: bool = True) -> Path:
    """ONE BUILD PER RUN, NOT PER WORKER (GOH.FSCHURN2, 2026-11-20). Each of a run's 12 workers
    built every template it met (`template-goh0` under its own basetemp): ~350 copied files, a
    `git add` of as many loose objects, and their 256 fan-out dirs, twelve times -- the largest
    single creator of the suite's file events (8% over a 60 s window of seven suites). The first
    worker to ask builds under an flock (`<key>.lock`, a blocking wait, never a poll) into a
    staging dir and publishes it with one rename, so a tree that exists is a whole one; the rest
    find it. `build` must make the same tree every time -- the contract it always had."""
    parent.mkdir(parents=True, exist_ok=True)
    done = parent / key
    if not done.is_dir():
        fd = os.open(parent / f"{key}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if not done.is_dir():
                stage = parent / f"{key}.build-{os.getpid()}"
                if stage.exists():
                    unsealed_rmtree(stage)
                stage.mkdir()
                with shared_setup():  # the run's build, not the asking test's (GOH.TESTWORK)
                    _build_quietly(build, stage / key, keep_quiet)
                os.rename(stage, done)
        finally:
            os.close(fd)
    return done / key


# GOH.TEMPLATEQUIET (2026-11-18): every commit a build makes detaches `git maintenance run`, which
# takes and drops .git/objects/maintenance.lock while the template is scanned and cloned (the
# 2026-10-08 race, fixed by hand in two fixtures and missed by the rest). The build runs under an
# XDG config that turns auto-maintenance and auto-gc off: git reads it for EVERY git the build
# spawns, and no env helper drops it (`hermetic_env(drop_git=True)` drops GIT_CONFIG_*, and
# `_fast_git` every GIT_*; neither touches XDG_CONFIG_HOME).
_QUIET_GIT_CONFIG = "[maintenance]\n\tauto = false\n[gc]\n\tauto = 0\n"


@contextlib.contextmanager
def quiet_git() -> Iterator[None]:
    """For the duration: every git started from this process reads a config with no background
    maintenance (the process's own environment is restored on exit)."""
    home = reused_mkdtemp("goh-quiet-git.")
    cfg = Path(home) / "git"
    cfg.mkdir()
    (cfg / "config").write_text(_QUIET_GIT_CONFIG)
    saved = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = home
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = saved
        shutil.rmtree(home, ignore_errors=True)


# Blocks (in a shell, so no test that reaches `template` is a polling one: the wait runs only when
# the quiet config did not hold, and tests/load_sensitive.py would mark every template user for it)
# until no git under $1 holds a background lock.
_WAIT_FOR_LOCKS = (
    'while [ -n "$(find "$1" \\( -name maintenance.lock -o -name gc.pid -o -name gc.log.lock \\) '
    '-print -quit)" ]; do sleep 0.02; done'
)


def _wait_quiescent(tree: Path) -> None:
    """Return once no git under `tree` holds a background lock: what a detached `git maintenance
    run` the build started anyway (a build that sets its own GIT_CONFIG_*) leaves behind. Bounded
    by the box's patience; a lock that never clears is a TimeoutExpired naming the command."""
    subprocess.run(
        ["sh", "-c", _WAIT_FOR_LOCKS, "sh", str(tree)],
        check=True, stdin=subprocess.DEVNULL, timeout=patience(30.0),
    )  # fmt: skip


def _keep_quiet(tree: Path) -> None:
    """Write the quiet config into every repository in `tree` (a work tree's .git, a bare origin):
    the clones inherit it, so a commit a TEST makes in one detaches no maintenance either."""
    for git_dir in _repositories(tree):
        with (git_dir / "config").open("a", encoding="utf-8") as fh:
            fh.write(_QUIET_GIT_CONFIG)


def _build_quietly(build, tree: Path, keep_quiet: bool = True) -> None:
    """Build the template `tree` with background git off, make it quiescent and keep it so in every
    repository it holds, then refuse it if it points at its own directory."""
    with _quiet_pack.aside():
        with quiet_git():
            build(tree)
        _wait_quiescent(tree)
        if keep_quiet:
            _keep_quiet(tree)
        _pack(tree)
        _tidy(tree)
    _refuse_self_reference(tree)
    _seal(tree)


def _repositories(tree: Path) -> Iterator[Path]:
    """Every git dir in `tree`: a work tree's .git, a bare origin."""
    for objects in Path(tree).rglob("objects"):
        git_dir = objects.parent
        if (
            (git_dir / "refs").is_dir()
            and (git_dir / "HEAD").is_file()
            and (git_dir / "config").is_file()
        ):
            yield git_dir


def _pack(tree: Path) -> None:
    """Every repository in `tree` with its objects in ONE pack (GOH.FSCHURN2): a commit of the
    goh estate left ~380 loose objects in up to 256 fan-out dirs, and every clone a test took was
    removed at its teardown file by file -- one file event each, in every test that used it. A
    repository with nothing loose is left alone (no git spawn). `-l`: never the objects a `--shared`
    clone borrows, which were 15 MB of this checkout's history packed into each such template."""
    for git_dir in _repositories(tree):
        loose = [
            d for d in (git_dir / "objects").iterdir() if len(d.name) == 2 and any(d.iterdir())
        ]
        if loose or len(list((git_dir / "objects").glob("pack/*.pack"))) > 1:
            subprocess.run(
                ["git", "--git-dir", str(git_dir), "repack", "-adlqn"],
                check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=patience(60.0),
                env=_no_git_env(),
            )  # fmt: skip
            subprocess.run(
                ["git", "--git-dir", str(git_dir), "prune-packed", "-q"],
                check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=patience(60.0),
                env=_no_git_env(),
            )  # fmt: skip
            for d in (git_dir / "objects").iterdir():
                if len(d.name) == 2 and d.is_dir() and not any(d.iterdir()):
                    d.rmdir()


def _tidy(tree: Path) -> None:
    """Every repository in `tree` in as few files as git keeps one in (GOH.FSEVQUIET): packed refs,
    no reflog (the suite's git keeps none, tests/suite_home.py), no note nothing reads, and no empty
    directory git makes again when it has something to put there. Each was one file event in every
    clone of it: 25 of a one-commit repository's, 10 now. A reflog a build asked for by name
    (core.logAllRefUpdates=always) stays."""
    for git_dir in _repositories(tree):
        if (git_dir / "packed-refs").exists() or _loose_refs(git_dir):
            subprocess.run(["git", "--git-dir", str(git_dir), "pack-refs", "--all", "--prune"],
                           check=True, capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=patience(60.0), env=_no_git_env())  # fmt: skip
        conf = git_dir / "config"
        text = conf.read_text()
        if "logAllRefUpdates = always" not in text:
            conf.write_text(text.replace("\tlogallrefupdates = true\n", ""))
            shutil.rmtree(git_dir / "logs", ignore_errors=True)
        for name in (
            "COMMIT_EDITMSG",
            "FETCH_HEAD",
            "description",
            "info/refs",
            "objects/info/packs",
        ):
            (git_dir / name).unlink(missing_ok=True)
        for part in ("info", "objects/info", "objects/pack", "refs"):
            _drop_empty(git_dir, part)


def _drop_empty(git_dir: Path, part: str) -> None:
    for here, _dirs, _files in os.walk(git_dir / part, topdown=False):
        if here != str(git_dir / "refs"):
            with contextlib.suppress(OSError):  # not empty
                os.rmdir(here)


def _loose_refs(git_dir: Path) -> bool:
    return any(files for _here, _dirs, files in os.walk(git_dir / "refs"))


def _no_git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _refuse_self_reference(tree: Path) -> None:
    """A template that records a path INTO its own private directory (a remote URL, a sibling bare
    origin, a worktree's gitdir, a config value) hands every clone a pointer back to shared state:
    a write through it lands there, and every later test sees it. Such a fixture stays per-test;
    this refuses it at build time. The directory is `tree`'s parent, which `from_template` made
    for this template alone, so any mention of it is one."""
    home = Path(tree).parent
    names = {os.fsencode(str(home)), os.fsencode(str(home.resolve()))}
    for path in Path(tree).rglob("*"):
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            if any(n in data for n in names):
                raise ValueError(
                    f"template {tree} names its own directory in {path}: not cloneable"
                )


_PROCESS_TEMPLATES: dict[str, Path] = {}
# The run's root inside a pytest session (conftest.py sets it from PYTEST_DEBUG_TEMPROOT): there a
# `process_template` is the whole RUN's, like `template` (GOH.FSCHURN3). Per process, the
# `todays-gates` checkout (~2,000 files) was built and removed by each of 12 workers: 2.3% of a
# whole run's file events. Outside a session (a helper's own subprocess) it stays per process.
_RUN_ROOT: list[Path] = []


def run_wide(root: Path) -> None:
    """Every later `process_template` in this process builds into `root`, once per run."""
    _RUN_ROOT[:] = [Path(root)]
    if _CLONEFILE is not None:
        _tree_pool.install(str(root), _clone_part, _is_sealed)


def run_head_cache() -> Path:
    """A HEAD-export cache (GOH_HEAD_CACHE) shared by the whole run, for a test that runs gates as a
    consumer from a checkout it never commits to (GOH.FSCHURN3). An export is named for its commit,
    renamed into place whole and never written again (gates/_from_head.sh), so the clones of one
    run-wide template share ONE export instead of each writing ~1,650 files and removing them. A
    test of the cache itself (its pruning, its build) keeps a private one."""
    # `_RUN_ROOT`, never PYTEST_DEBUG_TEMPROOT read now: tests/suite_tmp.py points that at the
    # worker's own dir for nested sessions, and every worker then built an export of its own.
    return (_RUN_ROOT[0] if _RUN_ROOT else Path(tempfile.gettempdir())) / "goh-head-cache"


def process_template(key: str, build, keep_quiet: bool = True) -> Path:
    """`template` for a helper called with only a path (no `tmp_path_factory`): built once per
    run under the session's root (`run_wide`), else once per process in a temp dir of its own,
    removed when the process exits. `keep_quiet=False` leaves the repositories' config as `build`
    wrote it (no maintenance-off keys), for a template that must be what git itself makes."""
    if key not in _PROCESS_TEMPLATES and _RUN_ROOT:
        _PROCESS_TEMPLATES[key] = _run_template(
            _RUN_ROOT[0] / "goh-process-templates", key, build, keep_quiet
        )
        _known_seals(_PROCESS_TEMPLATES[key])
    if key not in _PROCESS_TEMPLATES:
        home = Path(reused_mkdtemp("goh-template-" + key + "."))
        atexit.register(unsealed_rmtree, home)
        tree = home / key
        _build_quietly(build, tree, keep_quiet)
        _PROCESS_TEMPLATES[key] = tree
    return _PROCESS_TEMPLATES[key]
