"""`goh ship`'s release preflights (split from tools/ship.py by concern, GOH.CAPHEADROOM): goh's
changelog reading, then the repo's GOH_SHIP_PREFLIGHT, each run before anything is pushed; the
first refusal stops the ship. Pinned by tests/test_ship*.py.

goh ship's release preflights, split from `tools/ship.py` (GOH.CAPHEADROOM): goh's changelog
reading then the repo's `GOH_SHIP_PREFLIGHT`, run under the ship's bound before anything is pushed;
the first refusal stops the ship. Pinned by `test_ship_preflight_audit.py`.
"""

from __future__ import annotations

import argparse
import os
import shlex
import tempfile
from pathlib import Path

import bounded_run
import fail_lines
import ship_changelog
from ship_status import tail
from ship_version import SEMVER, Refused, git, git_bindings
from tmp_root import goh_tmp  # every temp root under $TMPDIR/goh.noindex (GOH.TMPNOINDEX)

from tui.lib import warn


def preflights(repo: Path, cfg: dict[str, str], version: str, summary="") -> list[tuple[str, str]]:
    """The commands asked before the push when the release step will run, in order: goh's
    changelog reading (GOH.SHIPUNRELEASED: a private Rust app's kit said `Unreleased is empty:
    nothing to release` after its push), then the repo's GOH_SHIP_PREFLIGHT, each as (whose,
    command). None when no release runs."""
    if not (cfg["GOH_SHIP_RELEASE"] and version):
        return []
    if any(SEMVER.match(t) for t in git(repo, "tag", "--points-at", "HEAD").splitlines()):
        return []
    try:
        out = ship_changelog.reading(repo, cfg["GOH_SHIP_CHANGELOG"], version, summary,
                                     cfg["GOH_CHANGELOG_FRAGMENTS"])  # fmt: skip
    except ValueError as e:
        raise Refused(str(e)) from None
    if cfg["GOH_SHIP_PREFLIGHT"].strip():
        out.append(("GOH_SHIP_PREFLIGHT", cfg["GOH_SHIP_PREFLIGHT"].replace("{version}", version)))
    return out


def run_preflights(repo: Path, cmds: list[tuple[str, str]], version: str, args: argparse.Namespace,
                   timeout: int, grace: int = 5) -> None:  # fmt: skip
    """Run each preflight under the ship's bound; the first refusal raises Refused, naming it, and
    what one that passed warned of is said (GOH.PINSWEEP)."""
    env = ["env", *(f"-u{k}" for k in git_bindings()), f"GOH_SHIP_VERSION={version}",
           f"GOH_SHIP_SUMMARY={args.summary or ''}",
           f"GOH_SHIP_ITEM={args.item or ''}"]  # fmt: skip
    cd = f"cd {shlex.quote(str(repo))} || exit 2; "
    for whose, cmd in cmds:
        fd, logname = tempfile.mkstemp(dir=goh_tmp(), prefix="goh-ship-preflight.", suffix=".log")
        os.close(fd)
        log = Path(logname)
        try:
            argv = [*env, "bash", "-c", cd + cmd]
            out = bounded_run.run_step(argv, timeout, grace, "ship preflight",
                                       str(log))  # fmt: skip
            kept = [fail_lines.ANSI.sub("", ln) for ln in tail(log) if ln.strip()]
            said, warned = kept[-8:], [ln[2:] for ln in kept if ln.startswith("⚠ ")]
        finally:
            log.unlink(missing_ok=True)
        if out.code == 0 and not out.timed_out:
            for ln in warned:
                warn(ln)
            continue
        what = f"timed out after {timeout}s" if out.timed_out else f"exit {out.code}"
        raise Refused(
            f"{repo.name}: the release preflight ({whose}) refuses v{version} before anything is "
            f"pushed ({what}): `{cmd}`\n  " + "\n  ".join(said or ["(it printed nothing)"])
        )
