"""A bare `python3 -m pytest` gets tools/pytest.sh's toolchain, or stops naming it (GOH.BAREPYTEST).

Two runners, two environments: tools/pytest.sh runs under bash 5 (lib/bash5.sh) from a shell whose
PATH has cargo, and a lane that ran `python3 -m pytest tests/test_release_gate.py` from another
PATH read a false red -- the /bin/bash 3.2 a test reached has no `mapfile`, and cargo was missing
(gates/_goh_bin.sh). Several of goh's own callers run pytest directly (lib/land_check.py,
lib/release_gate.py, tools/remote_shard.py), so a bare run is not refused: it is given the same
two things. Imported first by tests/suite_home.py, before any test or child reads PATH:

  * the bash >= 5 lib/bash5.sh chooses (its own answer, run directly) leads PATH, so every
    `bash` a test or gate spawns by name is that one;
  * cargo's directory (`$CARGO_HOME/bin`, else ~/.cargo/bin) is on PATH when no cargo is.

A machine with no bash >= 5 cannot be given one: the run stops at collection, naming
tools/pytest.sh and the fix, rather than failing test by test. Under the wrapper
(GATES_OF_HECK_SUITE_RUNNER) nothing changes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import MutableMapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = "GATES_OF_HECK_SUITE_RUNNER"


def bash5(environ: MutableMapping[str, str]) -> tuple[str | None, str]:
    """The bash >= 5 lib/bash5.sh would use, or None and what it said."""
    from timing_bounds import patience  # (no HOME read: the load average)

    probe = shutil.which("bash", path=environ.get("PATH")) or "/bin/bash"
    r = subprocess.run(
        [probe, str(ROOT / "lib" / "bash5.sh")],
        capture_output=True,
        text=True,
        check=False,
        env=dict(environ),
        stdin=subprocess.DEVNULL,
        timeout=patience(60),
    )
    found = r.stdout.strip()
    return (found or None) if r.returncode == 0 else None, (r.stderr or r.stdout).strip()


def same_env(environ: MutableMapping[str, str]) -> str | None:
    """Give a bare run the wrapper's bash and cargo; why it cannot, or None."""
    if environ.get(RUNNER):
        return None
    path = [p for p in environ.get("PATH", "").split(os.pathsep) if p]
    found, said = bash5(environ)
    if not found:
        return (
            f"no bash >= 5 for this bare pytest run ({said or 'lib/bash5.sh found none'}); "
            "run tools/pytest.sh, or install one (brew install bash)"
        )
    lead = str(Path(found).parent)
    path = [lead, *(p for p in path if p != lead)]
    if not shutil.which("cargo", path=os.pathsep.join(path)):
        home = environ.get("CARGO_HOME") or str(
            Path(environ.get("HOME", "~")).expanduser() / ".cargo"
        )
        if (Path(home) / "bin" / "cargo").exists():
            path.append(str(Path(home) / "bin"))
    environ["PATH"] = os.pathsep.join(path)
    return None


def enter() -> None:
    """At import of the suite: the same env, or stop at collection naming the runner."""
    if why := same_env(os.environ):
        import pytest

        pytest.exit(why, returncode=4)
