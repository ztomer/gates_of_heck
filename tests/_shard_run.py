"""A remote shard's own session (tools/remote_shard.py, GOH.READUNION): what its junit must tell.

The worker's passes join a land's record (lib/remote_lane.py), and the junit said too little of
them: not what the tests read, so a file it ran stood on main's old list (a wrong carry), and not
how many tests it had to run, so a shard whose workers died (`maximum crashed workers reached`: an
exit 1, one junit case per crash, the rest never run) read as "these failed, the others passed".
Under MARK, which the shard's `docker exec` sets:

  * the session watches its tests (tests/_read_audit.py `start`); each case's `goh_reads` property
    is what it read;
  * the suite's `goh_collected` property is how many tests the session collected, to hold the cases
    it ran against (lib/remote_lane.py `unfinished`).

MARK is taken out of the environment, so no session a test starts is a shard.
"""

from __future__ import annotations

import os

import pytest

MARK = "GATES_OF_HECK_REMOTE_SHARD"
COLLECTED = "goh_collected"  # lib/remote_lane.py reads it


def on(config) -> bool:
    """This session is a shard's controller; its count is said at the end."""
    if not os.environ.pop(MARK, ""):
        return False
    config.pluginmanager.register(Says(), "goh-shard-run")
    return True


class Says:
    @pytest.hookimpl(tryfirst=True)  # before junitxml writes its file
    def pytest_sessionfinish(self, session) -> None:
        xml = getattr(__import__("_pytest.junitxml", fromlist=["xml_key"]), "xml_key", None)
        log = session.config.stash.get(xml, None) if xml is not None else None
        if log is not None:
            log.add_global_property(COLLECTED, str(session.testscollected))
