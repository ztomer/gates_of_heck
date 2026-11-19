"""goh's own tree is shellchecked ONCE per suite session, in setup (GOH.CIRED01392).

A cold `goh shell-lint` of goh's tree is every one of its shell files (114) through shellcheck: 68 s
of CPU on the owner's box. The suite home is the session's, shared by its xdist workers and emptied
at exit (tests/suite_home.py), so the first test of a session to lint the tree in place paid that in
its CALL -- and at the hosted runner's one token (crates/goh/src/shellcheck_slots.rs, before
GOH.CIRED01392) `test_structural_full_leaves_nothing_in_tmpdir` took 224 s and 300 s against its
60 s ceiling (release CI, v0.139.2 and v0.139.3), whichever test scheduling put first.

`tree_lint` records the tree's verdicts in the session's verdict store (the home's
`.cache/goh/verdicts`, every worker's) under an flock, so one worker pays and the rest find them;
a test that lints the tree in place requests it and its gate reads them.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env


def shell_verdicts() -> Path:
    """The session's shell-lint verdict store: where a child with this HOME reads and writes."""
    return Path(os.environ["HOME"]) / ".cache" / "goh" / "verdicts" / "shell-lint"


def records() -> set[str]:
    return {p.name for p in shell_verdicts().rglob("*.json")}


@pytest.fixture(scope="session")
def tree_lint(goh) -> Path:
    store = shell_verdicts()
    store.parent.mkdir(parents=True, exist_ok=True)
    with (store.parent / "tree-lint.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # one worker lints; the others wait, then read
        r = subprocess.run([str(goh), "shell-lint"], cwd=REPO_ROOT, capture_output=True, text=True,
                           env=hermetic_env(), stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert records(), "goh shell-lint recorded no verdict in the session's store"
    return store
