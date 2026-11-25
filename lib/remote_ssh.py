"""The remote worker's ssh transport (tools/remote_shard.py, GOH.REMOTEMUX): `script` run by bash on
the worker host over one shared ControlMaster connection (ControlPersist, so no handshake per call).

THE SHARED MULTIPLEXER IS NO SINGLE POINT OF FAILURE. Concurrent pre-steps share one ControlMaster,
and on 2026-11-23 a session request through it failed (`mm_send_fd: sendmsg(2): Message too long`,
`mux_client_request_session: send fds failed`, ssh exit 255), which sent a whole share back to the
Mac. ssh exits 255 BEFORE the remote command starts when the multiplexer refuses a session, so a
fresh connection (no master, no socket) is retried once, and the rest of this run stays off the
multiplexer. Any other 255 (the host down) is still a fallback. The host is the caller's
(GOH_REMOTE_HOST, tools/remote_shard.py `conf`): nothing here names one.

The remote worker's ssh transport (GOH.REMOTEMUX, split from `tools/remote_shard.py` for its cap):
one shared ControlMaster connection; a multiplexer that refuses a session (ssh exit 255 with
`mux_client`/`mm_send_fd`) is retried once over a fresh connection, and the rest of the run stays
off it, so a broken multiplexer never sends a share back to the Mac. Pinned by
`test_remote_shard.py`.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tui.lib import warn

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ControlMaster=auto",
            "-o", "ControlPath=~/.ssh/goh-remote-%C", "-o", "ControlPersist=600"]  # fmt: skip
# ssh takes the FIRST value of an option, so the fresh options replace the shared ones.
FRESH = [*(o for pair in zip(SSH_OPTS[::2], SSH_OPTS[1::2], strict=True) if not pair[1].startswith(
    "Control") for o in pair), "-o", "ControlMaster=no", "-o", "ControlPath=none"]  # fmt: skip
MUX_FAILED = re.compile(r"mux_client|mm_send_fd|control ?socket|ControlPath", re.IGNORECASE)
_fresh: list[bool] = []  # set once the multiplexer failed in this run


class Fallback(Exception):
    """A reason the shard runs locally instead."""


def ssh(c: dict[str, str], script: str, stdin: bytes | None = None,
        timeout: float = 120) -> subprocess.CompletedProcess[bytes]:  # fmt: skip
    """`script` run by bash on the host (its login shell may be zsh), output captured; over the
    shared multiplexer, else (it failed, GOH.REMOTEMUX) over a fresh connection."""
    tail = [c["host"], "bash -c " + shlex.quote(script)]
    while True:
        opts = FRESH if _fresh else SSH_OPTS
        try:
            r = subprocess.run(["ssh", *opts, *tail], input=stdin, capture_output=True,
                               timeout=timeout,
                               stdin=None if stdin is not None else subprocess.DEVNULL,
                               check=False)  # fmt: skip
        except subprocess.TimeoutExpired as exc:
            raise Fallback(f"{c['host']} did not answer in {timeout:.0f} s") from exc
        err = r.stderr.decode(errors="replace")
        if _fresh or r.returncode != 255 or not MUX_FAILED.search(err):
            return r
        _fresh.append(True)
        said = " / ".join(ln.strip() for ln in err.splitlines() if MUX_FAILED.search(ln))
        warn(f"the shared ssh connection failed ({said[:200]}): a fresh connection instead")
