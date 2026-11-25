"""lib/idle_red.py -- a red idle full run files a fault item naming the lands since the last green
one (GOH.CARRYBASECODE).

The land gate judges a base-code change by its readers (tests/_carry_shadow.py `choose`): the set
that runs, F, is the tests that read the changed file, not the whole suite. The whole suite stays
the backstop, run on main's tip by the idle lander (lib/idle_suite.py) and by the ship. A fault only
an unchosen test sees therefore lands, and the idle run is where it turns red; this files it:

    file_red(main, green, sha, log)   `loopctl file` GOH.IDLERED<sha8>: the tests the log failed,
                                      and the first-parent commits green..sha -- the lands since the
                                      last green full run, one of which carried the fault; "filed
                                      ID", "already filed ID" or why not. Never raises.

GOH_LOOPCTL and GOH_LOOP_QUEUE are the seams (lib/reuse_fault.py `_file`). Pinned by
tests/test_idle_suite.py.

GOH.CARRYBASECODE: the land gate judges a base-code change by its readers (`tests/_carry_shadow.py`
`choose`), so the whole suite is its backstop on main. A red idle full run files
`GOH.IDLERED<sha8>` (`loopctl file`, seams `GOH_LOOPCTL`/`GOH_LOOP_QUEUE`) naming the failed tests
and the first-parent lands since the last green full run (`last.json` `green`). Pinned by
`test_idle_suite.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reuse_fault
import selection_miss

LANDS = 30  # commits named at most
FAILED = 10  # failed tests named at most


def lands(main: Path, green: str, sha: str) -> list[str]:
    """`<short sha> <subject>` for each first-parent commit GREEN..SHA, newest first; the last
    LANDS of SHA's history when no green full run is known."""
    span = [f"{green}..{sha}"] if green else [f"-{LANDS}", sha]
    r = subprocess.run(["git", "-C", str(main), "log", "--first-parent", "--format=%h %s", *span],
                       capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
                       env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})  # fmt: skip
    return r.stdout.splitlines() if r.returncode == 0 else []


def item(main: Path, green: str, sha: str, log: Path) -> dict:
    try:
        failed = selection_miss.failed_tests(log.read_text(errors="replace"))
    except OSError:
        failed = []
    named = lands(main, green, sha)
    since = f"since the last green full run at {green[:8]}" if green else (
        f"(no green full run recorded; the last {LANDS} commits)")  # fmt: skip
    more = f" (+{len(failed) - FAILED} more)" if len(failed) > FAILED else ""
    tests = ", ".join(failed[:FAILED]) + more if failed else "no failed test named (a gate step)"
    return {"id": f"GOH.IDLERED{sha[:8].upper()}", "repo": "gates_of_heck", "status": "todo",
            "depends": [], "source": "goh idle full suite (lib/idle_suite.py)",
            "item": f"The idle full suite on main {sha[:12]} went RED: {tests}. The land gate "
            f"judges a base-code change by its readers (GOH.CARRYBASECODE), so a fault only an "
            f"unchosen test sees lands and turns red here. The lands {since}: "
            f"{'; '.join(named[:LANDS]) or 'none read'}. Log: {log}. Find the land that broke "
            f"it, fix it, and make the selection choose that test for that change.",
            "metric": "idle full suite on main's tip: red runs (selection misses, "
                      "lib/selection_miss.py `full` rows)",
            "predicted": "the idle full suite green on main's tip; the missed test chosen",
            "done_state": "main's idle full suite green; a test pins the read the selection missed",
            "class_of": "none", "class_why": "one red full run on main"}  # fmt: skip


def sweep(main: Path, sha: str, log: Path) -> str:
    """THE POST-MERGE MAC SWEEP (GOH.REMOTETRUST, lib/remote_trust.py `reclassify`): a test this
    full Mac run failed in a platform_agnostic module takes the module to macos_only, so the land
    gate never trusts its Linux pass again; "" when none was agnostic."""
    try:
        import remote_trust  # lib/, on the path above

        failed = selection_miss.failed_tests(log.read_text(errors="replace"))
        moved = remote_trust.reclassify(main, failed, f"the full run on {sha[:8]}")
    except Exception as e:  # noqa: BLE001 -- the sweep's report never breaks the lander
        return f"macos_only sweep not run: {e.__class__.__name__}: {e}"
    return (
        f"now macos_only (failed on the Mac, its Linux pass trusted no more): {', '.join(moved)}"
        if moved
        else ""
    )


def offs() -> list[str]:
    """A reuse path off whose filing failed, filed now (reuse_audit.file_unfiled)."""
    try:
        import reuse_audit  # lib/, on the path above

        return [f"reuse audit: {line}" for line in reuse_audit.file_unfiled()]
    except Exception as e:  # noqa: BLE001 -- the report never breaks the lander
        return [f"reuse audit: an off path's fault not filed: {e.__class__.__name__}: {e}"]


def file_red(main: Path, green: str, sha: str, log: Path) -> str:
    try:
        loopctl = os.environ.get("GOH_LOOPCTL") or str(
            Path.home() / "Projects/.loop/tools/loopctl.py"
        )
        if not os.path.isfile(loopctl):
            return f"not filed: no loopctl at {loopctl}"
        it = item(main, green, sha, log)
        phase = reuse_fault.current_phase(set(it))
        if not phase.startswith("phase "):
            return f"not filed: {phase}"
        code, said = reuse_fault._file(loopctl, {**it, "phase": phase[6:]})
    except Exception as e:  # noqa: BLE001 -- the backstop's report never breaks the lander
        return f"not filed: {e.__class__.__name__}: {e}"
    if code == 0 or f"{it['id']} already exists" in said:
        return f"{'filed' if code == 0 else 'already filed'} {it['id']}"
    return f"not filed: {said[-200:]}"
