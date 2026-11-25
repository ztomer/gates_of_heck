"""The gates a test runs as a consumer: a clean checkout of them as they stand, built once a run and
cloned (split from tests/test_gate_environment.py, whose importers ran nothing else of it)."""

from __future__ import annotations

import functools
import hashlib
import shutil
import subprocess
from pathlib import Path

from _fast_git import fast_init
from _tree_clone import clone_tree, process_template

ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, check=True, stdin=subprocess.DEVNULL
    )


def _porcelain(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    ).stdout


def _clean_checkout_of_todays_gates(tmp_path: Path, rust: bool = True) -> Path:
    """A CLEAN git checkout of the gate as it stands, uncommitted work included. A clone
    of HEAD is wrong twice: it does not carry the gate under test mid-change, and every
    assertion would answer with the live tree.

    `--shared` borrows ROOT's objects (alternates) instead of copying them: `--no-hardlinks`
    copied the whole object store, 80 MiB of the ~100 MiB each of 35 tests left in a kept temp
    root (GOH.PYTESTTMP). The clone's own commits and the test's writes stay in its own store.

    Built ONCE per worker and clonefile'd per caller (GOH.FIXTURECOST): ~30 callers each cloned,
    diffed, applied and committed the same tree. `rust=False`: with no Rust source, 290 files fewer
    to remove and to export, for a test that names its binary and reads no stamp (GOH.FSEVQUIET)."""
    checkout = tmp_path / "gates_of_heck"
    checkout.parent.mkdir(parents=True, exist_ok=True)
    key, build = ("todays-gates", _build_clean_checkout)
    if not rust:
        key, build = ("todays-gates-no-rust", _build_no_rust)
    clone_tree(process_template(key, build), checkout)
    return checkout


def todays_gates_read() -> Path:
    """The run's ONE checkout of today's gates, for the modules that run them and never write there:
    a module's own was one a worker, 12,000 entries of a run's removal (GOH.FSEVQUIET). No clone of
    the template's: what a gate may leave in it reaches no other test's."""
    return process_template("todays-gates-read", _build_read)


def _build_read(checkout: Path) -> None:
    clone_tree(process_template("todays-gates", _build_clean_checkout), checkout)


# The data goh's scripts read by a name of their own directory's.
_DATA = ("lib/lock_order.tsv", "lib/goh-rustc", "gates/_export_bin_goh.sh")
_DATA_DIRS = ("lib/state_sql", "lib/goh_db_schema", "lib/goh_cache_schema")


def gates_part(dest: Path, *paths: str) -> Path:
    """The gates checkout a test of WHICH COMMIT judges needs: what PATHS run and read, committed as
    they stand at DEST, built once a run. The estate is 766 files in each export and work tree of
    it, a file event to write and one to remove (GOH.FSEVQUIET)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    build = functools.partial(_build_part, paths)
    clone_tree(process_template(f"gates-{_key(paths)}", build), dest)
    return dest


def _key(paths: tuple[str, ...]) -> str:
    return hashlib.sha256("\0".join(paths).encode()).hexdigest()[:12]


def land_gates(tmp_path: Path, *more: str) -> Path:
    """What a land of goh by goh reads: 180 files. Such a land exports its checkout once a commit
    and makes a work tree a lane, ten copies of it (8,600 file events of the whole estate). MORE:
    what else the test runs (lib/land_judge.py)."""
    paths = ("tools/land.sh", "tools/load.py", "lib/gate_identity.py", "lib/release_gate.py",
             *more)  # fmt: skip
    return gates_part(tmp_path / "gates_of_heck", *paths)


def _build_part(paths: tuple[str, ...], checkout: Path) -> None:
    import source_closure

    data = [str(p.relative_to(ROOT)) for d in _DATA_DIRS for p in (ROOT / d).glob("*.*")]
    source_closure.copy_closure(checkout, *paths, extra=(*_DATA, *data))
    fast_init(checkout, "main")
    _git(checkout, "add", "-A")
    _git(checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "today")


# THE ESTATE, not its suite (GOH.FSEVQUIET): no gate reads tests/, docs/ or the changelog's parts,
# and they were half the files every clone of this removes and every export writes. They are never
# written: the index is cut first, the work tree made from it.
_NOT_THE_ESTATE = ("tests", "changelog", "docs")


def _build_no_rust(checkout: Path) -> None:
    _build_clean_checkout(checkout, ("crates/*/src",))


def _build_clean_checkout(checkout: Path, less: tuple[str, ...] = ()) -> None:
    less = (*_NOT_THE_ESTATE, *less)
    but = [f":(exclude,glob){p}/**" for p in less]
    subprocess.run(
        ["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(checkout)],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    _git(checkout, "read-tree", "HEAD")
    _git(checkout, "rm", "-r", "-q", "--cached", "--", *(f":(glob){p}/**" for p in less))
    work = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "HEAD", "--binary", "--", ".", *but],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    ).stdout
    if work:
        subprocess.run(
            ["git", "-C", str(checkout), "apply", "--cached", "-"], input=work, check=True
        )
    _git(checkout, "checkout-index", "-a")
    # ...and the NEW files: `git diff HEAD` leaves out an untracked file, so a gate whose change
    # adds one (gates/_from_head.py did) was judged here without it -- and failed on the import.
    untracked = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "ls-files",
            "-z",
            "--others",
            "--exclude-standard",
            "--",
            ".",
            *but,
        ],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    ).stdout.split(b"\0")
    for rel in filter(None, (u.decode() for u in untracked)):
        (checkout / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, checkout / rel)
    _git(checkout, "add", "-A")
    # A clean working tree has NOTHING to commit and `git commit` says so with a
    # non-zero exit — and this fixture is built from a diff, so it is empty
    # exactly when the session has committed its work, which is the state this
    # file is developed in half the time. Commit only when there is something,
    # then insist the result is clean either way.
    if _porcelain(checkout):
        _git(checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "today")
    clean = _porcelain(checkout)
    assert not clean, f"the fixture checkout is not clean: {clean}"
