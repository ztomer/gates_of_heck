"""The two sides a release canary compares (scripts/release_canary.py), split from it at its line
cap: the native goh a revision builds, and that revision's gates, exported as a consumer gets
them."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "checks"))
from _gitutil import foreign_repo_env

BUILD_TIMEOUT_S = 1800


def git_run(where: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(where), *args],
        env=foreign_repo_env(),
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def resolve(goh: Path, rev: str) -> tuple[Path | None, str]:
    """(the native goh built from `rev`, why not): the gate's own resolver, which takes the build
    from the shared store or builds that commit into it (gates/_goh_bin.sh, build-goh.sh --rev)."""
    sha = git_run(goh, "rev-parse", "--verify", "-q", f"{rev}^{{commit}}").stdout.strip()
    if not sha:
        return None, f"{rev} is not a commit of {goh}"
    script = (
        '. "$1/gates/_goh_bin.sh" && _goh_resolve_uncached "$1/gates" "$2"'
        ' && printf "%s\\n%s" "$goh_native" "$goh_native_why"'
    )
    env = {k: v for k, v in foreign_repo_env().items() if not k.startswith("GOH_")}
    try:
        r = subprocess.run(
            ["bash", "-c", script, "_", str(goh), sha],
            env=env,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"resolving goh at {rev}: {e}"
    native, _, why = r.stdout.partition("\n")
    if native and os.access(native, os.X_OK):
        return Path(native), ""
    tail = (r.stderr.strip().splitlines() or [""])[-1]
    return None, f"no goh binary for {rev}: {why or tail or f'exit {r.returncode}'}"


def export(goh: Path, rev: str, dest: Path) -> str | None:
    """`git archive rev` into `dest` -- the gates the binary delegates to, as of that revision --
    shaped as a consumer's gate gets them (gates/_from_head.sh): `.goh-head` names the commit, so
    a gate script run from it runs in place and resolves that commit's binary. Without it a script
    there walked up for a checkout, found none, and judged differently (sys_updater's empty-scope
    probe read red here and green at its push)."""
    sha = git_run(goh, "rev-parse", "--verify", "-q", f"{rev}^{{commit}}").stdout.strip()
    if not sha:
        return f"{rev} is not a commit of {goh}"
    dest.mkdir(parents=True)
    # NOT `git archive | tar -x` (GOH.SUITEFLAKES). bsdtar exits at the archive's end marker
    # without draining the pipe, so git's last write of record padding, landing after that, takes
    # SIGPIPE: exit 141 with NOTHING on stderr, and `pipefail` made that the release canary's
    # "exporting v0.96.0: " with an empty reason (once, 2026-11-11). The archive is read whole,
    # then handed to tar, which may stop reading wherever it likes.
    archive = subprocess.run(
        ["git", "-C", str(goh), "archive", sha],
        env=foreign_repo_env(),
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if archive.returncode != 0:
        return f"exporting {rev}: {_failed('git archive', archive)}"
    unpack = subprocess.run(
        ["tar", "-x", "-C", str(dest)],
        input=archive.stdout,
        env=foreign_repo_env(),
        capture_output=True,
        check=False,
    )
    if unpack.returncode != 0:
        return f"exporting {rev}: {_failed('tar -x', unpack)}"
    (dest / ".goh-head").write_text(f"{sha}\n")
    return None


def _failed(what: str, r: subprocess.CompletedProcess) -> str:
    """A failed step, never an empty reason: its exit (a signal by name) and its stderr's tail."""
    why = r.stderr.decode("utf-8", "replace").strip() if isinstance(r.stderr, bytes) else r.stderr
    code = f"signal {-r.returncode}" if r.returncode < 0 else f"exit {r.returncode}"
    tail = " | ".join((why or "").strip().splitlines()[-3:])
    return f"{what} {code}: {tail or 'no stderr'}"
