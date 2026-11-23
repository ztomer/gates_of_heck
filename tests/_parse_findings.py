"""shellcheck's PARSE-time findings over many files, at the cost of parsing them (GOH.COVDEDUPE).

SC1091 (a sourced file not found) and SC1094 (a sourced file that does not parse) are raised while
shellcheck READS a script and what it sources; no analysis pass produces them. Yet a plain
`shellcheck -x --include=SC1091` runs the whole dataflow analysis and filters afterwards: over
this tree's 84 shell files that was 60 s of one core, and 181.7 s on the hosted runner, three
times the drift guard's ceiling. `--extended-analysis=false` turns the dataflow pass off (33 s
here, measured), and the files are judged in parallel, one shellcheck per chunk: a parse finding
belongs to the file being read, so how the files are split cannot change which ones are named.

KEPT BY WHAT SHELLCHECK COULD READ (GOH.SHELLCHECKCACHE). Inside a git checkout and under goh's
suite (tests/_kept_store.py, a store that outlives the run; CACHE_ENV names another), each file's
lines are kept under sha256 of: shellcheck's resolved path, size and mtime; the flags; the file's
path and bytes; every tracked file its source closure can reach (a tracked file whose name a
reached shell file's text mentions, followed out of shell files: an over-wide closure only misses
more); and every .shellcheckrc shellcheck could read (each parent's, the user's). A chunk whose
output names a file outside it keeps nothing. The probe's tree is no checkout, so the instrument
always runs.

`probe()` is the instrument's own red: a script sourcing a file that does not exist must come
back with SC1091 under the very flags the callers use, or a flag that silenced it would read as
"every source followed".
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import _kept_store
import suite_home

FLAGS = ("-x", "--extended-analysis=false", "--format=gcc")
CACHE_ENV = "GATES_OF_HECK_PARSE_FINDINGS_STORE"  # a test's own store (not GOH_: never scrubbed)
FORMAT = "parse-findings 1"
# What shellcheck may source and so must be followed: shell files, and extension-less ones
# (`.gatesrc`, a `lib/env` file).
SHELLISH = (".sh", ".bash", "")


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


def _run(files: list[str], cwd: Path, extra: tuple[str, ...]) -> dict[str, list[str]] | None:
    """Each file's lines, parsed in parallel chunks; None when a line names no file of its chunk
    (it cannot be attributed, so nothing of the run is kept)."""
    jobs = max(1, min(len(files), os.cpu_count() or 1))
    chunks = [files[i::jobs] for i in range(jobs)]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        outs = list(pool.map(lambda c: _one(c, cwd, extra), chunks))
    by: dict[str, list[str]] | None = {f: [] for f in files}
    for chunk, out in zip(chunks, outs, strict=True):
        for line in filter(None, out.splitlines()):
            owner = next((f for f in chunk if line.startswith(f"{f}:")), None)
            if owner is None or by is None:
                by = None
                continue
            by[owner].append(line)
    if by is None:
        return {"": [ln for out in outs for ln in out.splitlines() if ln]}
    return by


def _store() -> str:
    """Where the records live: CACHE_ENV's directory, else under goh's suite its kept store; ""
    (nothing kept) outside the suite."""
    if named := os.environ.get(CACHE_ENV, ""):
        return named
    if os.environ.get(CACHE_ENV + "_OFF") or not os.environ.get(suite_home.MARKER):
        return ""
    return str(_kept_store.kept("parse-findings"))


def _tool() -> str | None:
    found = shutil.which("shellcheck", path=_env().get("PATH"))
    if not found:
        return None
    real = os.path.realpath(found)
    st = os.stat(real)
    return f"{real}:{st.st_size}:{st.st_mtime_ns}"


def _tracked(cwd: Path) -> list[Path] | None:
    r = subprocess.run(
        ["git", "-C", str(cwd), "ls-files", "-z", "--full-name", ":/"],
        capture_output=True, check=False, stdin=subprocess.DEVNULL,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    )  # fmt: skip
    top = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    ).stdout.strip()  # fmt: skip
    if r.returncode or not top:
        return None
    return [Path(top) / f for f in r.stdout.decode().split("\0") if f]


def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "-"


class _Keys:
    """The key of each file of one checkout (module doc)."""

    def __init__(self, cwd: Path, tracked: list[Path], head: str) -> None:
        self.cwd, self.head = cwd, head
        self.named: dict[str, list[Path]] = {}
        self.memo: dict[Path, list[Path]] = {}
        for p in tracked:
            self.named.setdefault(p.name, []).append(p)

    def _edges(self, path: Path) -> list[Path]:
        if path not in self.memo:
            self.memo[path] = self._read_edges(path)
        return self.memo[path]

    def _read_edges(self, path: Path) -> list[Path]:
        if path.suffix not in SHELLISH:
            return []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return [p for name, ps in self.named.items() if name in text for p in ps]

    def key(self, rel: str) -> str:
        start = Path(os.path.normpath(self.cwd / rel))
        seen: dict[Path, None] = {}
        todo = [start]
        while todo:
            p = todo.pop()
            if p not in seen:
                seen[p] = None
                todo.extend(q for q in self._edges(p) if q not in seen)
        home = Path(os.environ.get("HOME", "/"))
        xdg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
        rcs = [*(d / ".shellcheckrc" for d in start.parents), home / ".shellcheckrc"]
        rcs.append(xdg / "shellcheckrc")
        parts = [self.head, rel, *(f"{p}={_digest(p)}" for p in sorted(seen))]
        parts += [f"{p}={_digest(p)}" for p in rcs if p.is_file()]
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def findings(files: list[str], cwd: Path, *extra: str) -> list[str]:
    """Every gcc-format line shellcheck prints for `files` (relative to `cwd`, or absolute)."""
    if not files:
        return []
    store = _store()
    tool = _tool() if store else None
    tracked = _tracked(cwd) if tool else None
    if not tracked:
        by = _run(files, cwd, extra)
        return [line for f in by for line in by[f]] if by else []
    keys = _Keys(cwd, tracked, "|".join([FORMAT, tool, *FLAGS, *extra]))
    home = Path(store)
    kept: dict[str, list[str]] = {}
    want = {f: keys.key(f) for f in files}
    for f, k in want.items():
        with contextlib.suppress(OSError, ValueError):
            kept[f] = json.loads((home / k[:2] / f"{k}.json").read_text())
    misses = [f for f in files if f not in kept]
    fresh = _run(misses, cwd, extra) if misses else {}
    if fresh and "" not in fresh:
        for f, lines in fresh.items():
            dest = home / want[f][:2] / f"{want[f]}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(lines))
            os.replace(tmp, dest)
    out = {**kept, **(fresh or {})}
    return [line for f in [*files, ""] for line in out.get(f, [])]


def probe(tmp: Path, *extra: str) -> list[str]:
    """The same flags over a script whose source is missing: must name SC1091."""
    tmp.mkdir(parents=True, exist_ok=True)
    script = tmp / "probe_missing_source.sh"
    script.write_text('#!/usr/bin/env bash\n. "$(dirname "${BASH_SOURCE[0]}")/absent.sh"\n')
    return findings([str(script)], tmp, *extra)
