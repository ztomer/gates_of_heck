"""shellcheck's PARSE-time findings over many files, at the cost of parsing them (GOH.COVDEDUPE).

SC1091 (a sourced file not found) and SC1094 (a sourced file that does not parse) are raised while
shellcheck READS a script and what it sources; no analysis pass produces them. Yet a plain
`shellcheck -x --include=SC1091` runs the whole dataflow analysis and filters afterwards: over
this tree's 84 shell files that was 60 s of one core, and 181.7 s on the hosted runner, three
times the drift guard's ceiling. `--extended-analysis=false` turns the dataflow pass off (33 s
here, measured), and the files are judged in parallel, one shellcheck per chunk: a parse finding
belongs to the file being read, so how the files are split cannot change which ones are named.

`probe()` is the instrument's own red: a script sourcing a file that does not exist must come
back with SC1091 under the very flags the callers use, or a flag that silenced it would read as
"every source followed".
"""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FLAGS = ("-x", "--extended-analysis=false", "--format=gcc")


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "SHELLCHECK_OPTS"}


def _one(files: list[str], cwd: Path, extra: tuple[str, ...]) -> str:
    r = subprocess.run(
        ["shellcheck", *FLAGS, *extra, *files],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env=_env(),
    )
    return r.stdout


def findings(files: list[str], cwd: Path, *extra: str) -> list[str]:
    """Every gcc-format line shellcheck prints for `files` (relative to `cwd`, or absolute)."""
    if not files:
        return []
    jobs = max(1, min(len(files), os.cpu_count() or 1))
    chunks = [files[i::jobs] for i in range(jobs)]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        outs = list(pool.map(lambda c: _one(c, cwd, extra), chunks))
    return [line for out in outs for line in out.splitlines() if line]


def probe(tmp: Path, *extra: str) -> list[str]:
    """The same flags over a script whose source is missing: must name SC1091."""
    tmp.mkdir(parents=True, exist_ok=True)
    script = tmp / "probe_missing_source.sh"
    script.write_text('#!/usr/bin/env bash\n. "$(dirname "${BASH_SOURCE[0]}")/absent.sh"\n')
    return findings([str(script)], tmp, *extra)
