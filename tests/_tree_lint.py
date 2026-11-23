"""goh's own tree is shellchecked ONCE per suite session, in setup (GOH.CIRED01392).

A cold `goh shell-lint` of goh's tree is every one of its shell files (114) through shellcheck: 68 s
of CPU on the owner's box. The suite home is the session's, shared by its xdist workers and emptied
at exit (tests/suite_home.py), so the first test of a session to lint the tree in place paid that in
its CALL -- and at the hosted runner's one token (crates/goh/src/shellcheck_slots.rs, before
GOH.CIRED01392) `test_structural_full_leaves_nothing_in_tmpdir` took 224 s and 300 s against its
60 s ceiling (release CI, v0.139.2 and v0.139.3), whichever test scheduling put first.

`tree_lint` records the tree's verdicts in the session's verdict store (the home's
`.cache/goh/verdicts`, every worker's) under an flock, so one worker pays and the rest find them;
a test that lints the tree in place requests it and its gate reads them. A test that asserts its
gate recorded nothing copies the store first (`private_copy`): every worker writes the session's,
so a verdict another test records during the call would read as this one's (the first land of
GOH.CIRED01392 went red on "shellchecked 2 files" that way).

The session's records are also KEPT across sessions (tests/_kept_store.py, GOH.SHELLCHECKCACHE):
`tree_lint` seeds the session's store from the kept one and keeps what a green lint recorded, so a
session lints only the shell files whose bytes (or sources, tools, rc files) are new.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
from pathlib import Path

import _kept_store
import pytest
from conftest import REPO_ROOT, hermetic_env


def shell_verdicts() -> Path:
    """The session's shell-lint verdict store: where a child with this HOME reads and writes."""
    return Path(os.environ["HOME"]) / ".cache" / "goh" / "verdicts" / "shell-lint"


def records(store: Path | None = None) -> set[str]:
    """The verdict records in `store` (default: the session's)."""
    return {p.name for p in (store or shell_verdicts()).rglob("*.json")}


@pytest.fixture(scope="session")
def tree_lint(goh) -> Path:
    store = shell_verdicts()
    store.parent.mkdir(parents=True, exist_ok=True)
    kept = _kept_store.kept("tree-lint-verdicts")
    with (store.parent / "tree-lint.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # one worker lints; the others wait, then read
        _merge(kept, store)  # an earlier session's verdicts: a record is its whole key's
        r = subprocess.run([str(goh), "shell-lint"], cwd=REPO_ROOT, capture_output=True, text=True,
                           env=hermetic_env(), stdin=subprocess.DEVNULL, check=False)  # fmt: skip
        if r.returncode == 0:
            _merge(store, kept)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert records(), "goh shell-lint recorded no verdict in the session's store"
    return store


def _merge(src: Path, dest: Path) -> None:
    """Every verdict record of SRC that DEST lacks, copied (GOH.SHELLCHECKCACHE). A record is keyed
    by every input of its verdict (crates/goh/src/shell_lint.rs `judge`), so one kept from an
    earlier session is the verdict this one would compute: a warm tree lint execs no shellcheck."""
    for rec in src.rglob("*.json"):
        to = dest / rec.relative_to(src)
        if not to.exists():
            to.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(rec, to)


def private_copy(store: Path, dest: Path) -> Path:
    """`store` copied under `dest` as a GOH_VERDICT_DIR of its own; the copy's shell-lint dir."""
    shutil.copytree(store, dest / "shell-lint")
    return dest / "shell-lint"
