"""Capture a process environment without capturing its secrets (GOH.ENVDUMP).

A test that asks "what environment did the child get?" used to answer it with `env > file`: the
WHOLE environment, session tokens included, written into a temp dir that a killed run left behind
(tests/test_coverage_external.py, 2026-11-12), and read back into a dict that pytest prints whole
when an assertion on it fails (tests/test_launch_app.py, GOH.CLEANOPEN). A test needs the NAMES,
plus the values of the few names it asserts on. So this is the one way to capture an environment:

  * `probe_sh(out, keep=(...))` -- a POSIX sh command that writes every variable's NAME to `out`,
    and `NAME=VALUE` only for the names in `keep`; written to `out.tmp` and moved, so a reader
    polling for `out` never reads half of it;
  * `read_probe(out)` -- that file as a dict: the kept names map to their values, every other name
    to `REDACTED`, so `"X" in seen`, `seen.get("X")` and a failing assertion that prints the whole
    dict all work and none of them carries a value nobody asked for;
  * `redacted(env, keep=(...))` -- the same dict from an in-process mapping (`os.environ`, a
    `hermetic_env()`), for a test that would otherwise put one in an assertion message.

tests/test_no_env_dump.py refuses a test that dumps an environment any other way.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Mapping
from pathlib import Path

REDACTED = "<redacted>"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _names(keep: Iterable[str]) -> list[str]:
    names = list(keep)
    bad = [n for n in names if not _NAME.match(n)]
    if bad:
        raise ValueError(f"not environment variable names: {bad}")
    return names


def probe_sh(out: Path, keep: Iterable[str] = ()) -> str:
    """A POSIX sh command: every name in its environment, and the values of `keep` only."""
    tmp, dest = shlex.quote(f"{out}.tmp"), shlex.quote(str(out))
    values = "".join(
        f'if [ -n "${{{n}+x}}" ]; then printf \'%s=%s\\n\' {n} "${n}"; fi; ' for n in _names(keep)
    )
    names = "/usr/bin/awk 'BEGIN { for (k in ENVIRON) print k }'"
    return f"{{ {names}; {values}}} > {tmp} && mv {tmp} {dest}"


def read_probe(out: Path) -> dict[str, str]:
    """`out` as written by probe_sh: kept names to their values, the rest to REDACTED."""
    seen: dict[str, str] = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        name, eq, value = line.partition("=")
        if eq:
            seen[name] = value
        elif line:
            seen.setdefault(name, REDACTED)
    return seen


def redacted(env: Mapping[str, str], keep: Iterable[str] = ()) -> dict[str, str]:
    """`env` with every value but those of `keep` replaced by REDACTED."""
    kept = set(_names(keep))
    return {k: (v if k in kept else REDACTED) for k, v in env.items()}
