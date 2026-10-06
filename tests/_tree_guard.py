"""The suite never moves the checkout it tests (imported into conftest as session hooks).

THE CLASS. A test that edits a tracked file of this repo "and restores it in a finally" still
changes the tree for its whole duration, under every gate and every other worker reading it:
`test_release_hardening.py` rewrote the real `tools/release-kit/release.sh` mid-run, and the rust
gate -- whose coverage run executes this suite -- refused its own pass, "the tree moved under the
gate" (2026-10-06). A test that drops a file into the checkout (an instrumented binary's
`default_*.profraw`) is the same class. So the controller stamps the tree (`lib/tree_stamp.py`,
the gates' own instrument) at session start, compares at the end, and fails the session naming
every path that moved. A test works on a COPY under tmp_path.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
import tree_stamp  # noqa: E402

_KEY = "_goh_tree_stamp"


def pytest_sessionstart(session) -> None:
    if not hasattr(session.config, "workerinput"):  # the controller only: one stamp per run
        setattr(session.config, _KEY, tree_stamp.fingerprint(REPO_ROOT))


def pytest_sessionfinish(session, exitstatus) -> None:
    before = getattr(session.config, _KEY, None)
    if before is None:
        return
    changed = tree_stamp.moved(before, tree_stamp.fingerprint(REPO_ROOT))
    if changed:
        named = "\n".join(f"    {p}" for p in changed[: tree_stamp.MAX_NAMED])
        sys.stderr.write(
            f"\n✗ the suite moved the checkout it tests -- {len(changed)} path(s):\n{named}\n"
            "✗ a test must work on a copy under tmp_path, never on this repo's own files\n"
            "  (or the tree was edited while the suite ran: re-run it with the tree still)\n"
        )
        session.exitstatus = 1
