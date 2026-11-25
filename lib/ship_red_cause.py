"""lib/ship_red_cause.py -- what a ship's red suite run already names, read from its log, so the
worker bisects only a red that names no culprit, and then narrowly (GOH.SHIPBISECTCOV).

2026-11-22: the ship of 84cbc55f went red at diff coverage, its log naming ten uncovered lines by
file:line, and its worker then bisected the lands since the last ship, a full instrumented suite
(~16 min) per step, each step judging "no changed Rust line", holding the ship lock for 50 min.

    judged(log)          the commit the suite ran on, as its log says: main may have moved since
    uncovered(log)       the diff coverage red's uncovered (file, line, text), gates/diff_cov.py's
                         print, at any indent of 4 or more; [] when the log has none
    attribute(...)       per uncovered line, the commit `git blame` names at the tip and the land
                         (lib/ship_full_run.py `lands`) that first carried it: "" when it is older
                         than the last ship
    failed_tests(log)    the node ids pytest's summary names FAILED or ERROR, at any indent, spaced
                         parameters whole: the narrow bisect's set; [] when a Rust test is red too
                         (pytest alone would not see that red: the full run is the probe)
    probe(wt, ids)       those tests alone, plain, in the ship's worktree: 0 green, 1 red, 2 none;
                         one pytest says the commit has not is not red there (`absent`)

Pinned by tests/test_ship_red_cause.py and tests/test_ship_red_replay.py.

GOH.SHIPBISECTCOV: what a ship's red suite log already names: `uncovered` (the diff coverage red's
file:line rows), `attribute` (per row the `git blame` commit at the tip and the first land since
the last ship carrying it), `failed_tests` (pytest's FAILED node ids), `probe` (those tests alone,
plain, in the ship's worktree: one narrow bisect step, a test absent there not red), `judged` (the
commit the suite ran on). Pinned by `test_ship_red_cause.py`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import main_red  # lib/: the test ids a log names FAILED or ERROR

ROOT = Path(__file__).resolve().parent.parent
_HEAD = re.compile(r"\[diff-coverage\] \d+ of \d+ changed executable lines? uncovered")
# A row as gates/diff_cov.py prints it, or deeper under a step that indents its log; a path with a
# space; a line of no text, its trailing blanks gone (GOH.SUITEREDEARLY).
_LINE = re.compile(r"^ {4,}(\S.*?):(\d+)(?:  (.*))?\s*$")
# A Rust test red beside pytest's: libtest's own line, or coverage_gate's list of them.
_RUST = re.compile(
    r"^\s*test \S+ \.\.\. FAILED\s*$|TESTS FAILED under coverage: (?!the run names)\S"
)
_JUDGED = re.compile(r"^ship full suite on ([0-9a-f]{7,40}) in ")
NOT_FOUND = 4  # pytest's exit when a named id is not there: it then runs none of them
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
            out.append((m[1], int(m[2]), (m[3] or "").strip()))
    return out


def judged(log: str) -> str:
    """The commit (its short or whole name) the log's last judged run ran on, or ""."""
    return next((m[1] for ln in reversed(log.splitlines()) if (m := _JUDGED.match(ln))), "")


def failed_tests(log: str) -> list[str]:
    if any(_RUST.search(ln) for ln in log.splitlines()):
        return []
    found = main_red.nodes_of(log)  # the one reader of pytest's summary, a spaced parameter whole
    cut = {n for n in found if any(o.startswith(f"{n}[") for o in found)}  # a list cut that one
    return [n for n in found if n not in cut]


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


def absent(ids: list[str], out: str) -> list[str]:
    """The IDS a pytest that exited 4 says are `not found` in its tree: the whole id, or its
    function or file (then every parameter of it)."""
    key = "not found: "
    said = [ln.partition(key)[2].strip() for ln in out.splitlines() if key in ln]
    return [n for n in ids if any(f"/{s}".endswith((f"/{n}", f"/{n.split('[', 1)[0]}"))
                                  or s == n.split("::", 1)[0] for s in said)]  # fmt: skip


def _pytest(wt: Path, ids: list[str], max_s: str) -> tuple[int, str]:
    runner = ["bash", "tools/pytest.sh"] if (wt / "tools" / "pytest.sh").is_file() else [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]  # fmt: skip
    argv = [sys.executable, str(ROOT / "tools" / "load.py"), "--background-ok", "ship bisect step",
            "--max", max_s, "0", "--", *runner, *ids]  # fmt: skip
    said: list[str] = []
    with subprocess.Popen(argv, cwd=wt, env=dict(os.environ, GOH_GATE_CLASS="ship"), text=True,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, errors="replace",
                          stderr=subprocess.STDOUT) as run:  # fmt: skip
        for line in run.stdout:
            print(line, end="", flush=True)
            said.append(line)
    return run.returncode, "".join(said)


def probe(wt: Path, ids: list[str], max_s: str) -> int:
    """IDS alone, plain, in WT (forced to the commit judged): GREEN, RED or NO_VERDICT. An id WT's
    tree has not is no red of that commit: pytest, named one, runs none at all (exit 4), so a land
    older than one of the tests gave no verdict and ended the bisect (GOH.SUITEREDEARLY)."""
    while ids:
        rc, out = _pytest(wt, ids, max_s)
        gone = absent(ids, out) if rc == NOT_FOUND else []
        if not gone:
            return {0: GREEN, 1: RED}.get(rc, NO_VERDICT)
        print(f"ship bisect: not in {wt.name}'s tree: {' '.join(gone)}", flush=True)
        ids = [n for n in ids if n not in gone]
    return GREEN
