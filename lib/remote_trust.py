"""lib/remote_trust.py -- which tests' Linux verdicts count at the land gate (GOH.REMOTETRUST).

OWNER, 2026-11-23, ruled ("adopt neon's ideas as well"; "Yes, it's ruled"), after GOH.REMOTESAVED's
measurement: it replaces GOH.REMOTESHARE's "accept the rerun". The gate re-ran on the Mac every test
the Linux worker had passed in the enqueue's pre-step, so the workers shortened only the pre-step
and the lock-held gate did not move: over the 20 lander runs to 2026-11-24 the gate's suite took
the enqueue's verdict in none, 7 of them refused for the worker's passes alone.

* THE CLASSES (`platform`). A test is `platform_agnostic` when its module is recorded Linux-safe
  (tests/linux_safe.txt: it passed whole on the worker with the Mac's skips) and the disagreement
  ledger (goh.db `linux_unsafe`, lib/remote_lane.py `unsafe`) does not name it; every other test is
  `macos_only`. One list and one ledger: the split that sends tests and the gate that trusts them
  read the same two, so a module that leaves the list is neither sent nor trusted.
* THE GATE (lib/pre_reuse.py `suite`). The enqueue's record keeps the worker's passes apart
  (`remote`); the gate's suite takes them as this tree's verdict when every one is
  platform_agnostic, and runs the suite on the Mac when any is macos_only (`split`). The suite
  record it writes is never `full`, so `goh ship` still runs the whole suite on the Mac before any
  tag: a land's verdict may be Linux's, a release's never is.
* THE SWEEP (`reclassify`). The post-merge full Mac runs -- the idle lander's (lib/idle_suite.py)
  and the ship's -- are the periodic audit: a test that fails there in a platform_agnostic module
  takes its module into the ledger, macos_only from then on, so a Linux-only pass that a land
  trusted is caught at the next full run and never trusted again. The gate's sampled shadow
  (lib/remote_gate.py, one gate in 10) adds the same way.

Pinned by tests/test_remote_trust.py.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import remote_gate, remote_lane

AGNOSTIC, MAC_ONLY = "platform_agnostic", "macos_only"


def platform(root: Path, node: str, safe: set[str] | None = None) -> str:
    """A test's class (module doc); SAFE, the agnostic modules, when the caller read them once."""
    if safe is None:
        safe = agnostic_modules(root)
    return AGNOSTIC if remote_lane.module(node) in safe else MAC_ONLY


def agnostic_modules(root: Path) -> set[str]:
    return remote_lane.listed(root, remote_lane.LINUX_SAFE) - remote_lane.unsafe()


def split(root: Path, nodes: Iterable[str]) -> tuple[list[str], list[str]]:
    """(the nodes whose Linux verdict counts, the macos_only ones), each sorted."""
    safe = agnostic_modules(root)
    trusted, mac = [], []
    for n in sorted(set(nodes)):
        (trusted if platform(root, n, safe) == AGNOSTIC else mac).append(n)
    return trusted, mac


def reclassify(root: Path, failed: Iterable[str], why: str) -> list[str]:
    """The modules of FAILED that were platform_agnostic, now in the ledger (macos_only), each with
    WHY and its first failed node; [] when none was. Never raises: the sweep's report is not its
    verdict."""
    safe = agnostic_modules(root)
    found: dict[str, str] = {}
    for node in failed:
        mod = remote_lane.module(node)
        if mod in safe and mod not in found:
            found[mod] = f"{node} failed on the Mac in {why}"
    if found:
        try:
            remote_gate.record_unsafe(found)
        except Exception:  # noqa: BLE001 -- a lost ledger row costs one trusted module, never a run
            return []
    return sorted(found)
