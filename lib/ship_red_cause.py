"""lib/ship_red_cause.py -- what a ship's red suite run already names, read from its log, so the
worker bisects only a red that names no culprit, and then narrowly (GOH.SHIPBISECTCOV).

2026-11-22: the ship of 84cbc55f went red at diff coverage, its log naming ten uncovered lines by
file:line, and its worker then bisected the lands since the last ship, a full instrumented suite
(~16 min) per step, each step judging "no changed Rust line", holding the ship lock for 50 min.

    uncovered(log)       the diff coverage red's uncovered (file, line, text), gates/diff_cov.py's
                         print; [] when the log has none
    attribute(...)       per uncovered line, the commit `git blame` names at the tip and the land
                         (lib/ship_full_run.py `lands`) that first carried it: "" when it is older
                         than the last ship
    failed_tests(log)    the node ids pytest's summary names FAILED or ERROR, at any indent: the
                         narrow bisect's set
    probe(wt, ids)       those tests alone, plain, in the ship's worktree: 0 green, 1 red, 2 none

Pinned by tests/test_ship_red_cause.py.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_HEAD = re.compile(r"\[diff-coverage\] \d+ of \d+ changed executable lines? uncovered")
_LINE = re.compile(r"^\s{4}(\S+?):(\d+)  (.*)$")
# pytest's summary line, as tools/pytest.sh prints it (indented under its step: GOH.BISECTRECHECK,
# the v0.150.0 ship's log named its failing test at 4 spaces and the bisect saw none); an ERROR
# node (a setup error) is a failing test too.
_FAILED = re.compile(r"^\s*(?:FAILED|ERROR) (\S+::\S+)")
GREEN, RED, NO_VERDICT = 0, 1, 2


def uncovered(log: str) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    lines = log.splitlines()
    for i, ln in enumerate(lines):
        if not _HEAD.search(ln):
            continue
        for row in lines[i + 1 :]:
            m = _LINE.match(row)
            if not m:
                break
            out.append((m[1], int(m[2]), m[3].strip()))
    return out


def failed_tests(log: str) -> list[str]:
    return list(dict.fromkeys(m[1] for ln in log.splitlines() if (m := _FAILED.match(ln))))


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def attribute(
    repo: Path, head: str, lands: list[str], rows: list[tuple[str, int, str]]
) -> list[dict]:
    """Per uncovered row, the blamed commit and the first of LANDS (oldest first) carrying it."""
    out = []
    for path, line, text in rows:
        blame = _git(repo, "blame", "--porcelain", "-L", f"{line},{line}", head, "--", path)
        commit = blame.stdout.split(" ", 1)[0] if blame.returncode == 0 else ""
        land = next((c for c in lands if commit and _carries(repo, c, commit)), "")
        subject = _git(repo, "log", "-1", "--format=%s", land).stdout.strip() if land else ""
        out.append({"file": path, "line": line, "text": text, "commit": commit, "land": land,
                    "subject": subject})  # fmt: skip
    return out


def _carries(repo: Path, land: str, commit: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", commit, land).returncode == 0


def say(row: dict) -> str:
    """One uncovered line and its land, for the log and the red's tail."""
    who = f"land {row['land'][:12]} ({row['subject']})" if row["land"] else "before the last ship"
    return f"uncovered {row['file']}:{row['line']} -> {who}"


def probe(wt: Path, ids: list[str], max_s: str) -> int:
    """IDS alone, plain, in WT (forced to the commit judged): GREEN, RED or NO_VERDICT."""
    runner = ["bash", "tools/pytest.sh"] if (wt / "tools" / "pytest.sh").is_file() else [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]  # fmt: skip
    argv = [sys.executable, str(ROOT / "tools" / "load.py"), "--background-ok", "ship bisect step",
            "--max", max_s, "0", "--", *runner, *ids]  # fmt: skip
    rc = subprocess.run(argv, cwd=wt, env=dict(os.environ, GOH_GATE_CLASS="ship"),
                        stdin=subprocess.DEVNULL, check=False).returncode  # fmt: skip
    return {0: GREEN, 1: RED}.get(rc, NO_VERDICT)
