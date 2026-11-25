"""lib/selection_miss.py -- the selection-miss ledger and the full run's shadow (GOH.SELECTAUDIT).

The pre-step (tools/affected_tests.py) runs the tests a diff chooses; a test it did not choose
that goes red later costs a whole land gate. Nothing counted those, so whether the selector is
safe was a feeling. Here every judged red is read against the selection its diff makes:

    judge(...)        the failed tests of a log (pytest's `FAILED tests/...` summary lines, the
                      gate's failure-lines copy included) against the selection: one `judged` row
                      (source, item, sha, day, changed, selected, failed, missed) and one `miss`
                      row per failed test the selection did not choose
    record_land(...)  tools/landq.py, on every red land attempt (its own log and each red batch
                      gate run its stack was in, attempt_logs): the selection is recomputed by
                      the tip's OWN selector over `git archive <sha>` for the diff from main's
                      merge base -- the set the item's pre-step ran
    shadow(...)       lib/idle_suite.py, after each idle full run on main: the selection for the
                      diff since the last full run, against that run's failures. A measure, never
                      a skip: the full suite still runs every test
    replay(...)       the land records' red attempts, base from main's reflog at the enqueue
                      (`python3 lib/selection_miss.py --replay --since DAY`)

Ledger: goh.db's selection_misses (lib/land_records.py; the file `<landq>/selection_misses.jsonl`
before GOH.STATEDB); `goh land --report` shows misses per day (summary()).
A red gate STEP (no failed test: GOH.PYTESTPIN's canary) writes a `step` row: the step, whether the
pre-step ran it (PRE_RAN), the changed files; one the pre-step never ran is a miss, counted in
summary()'s `missed` and `steps` (GOH.MISSLEDGERGATE). A test the selection chose is never a miss,
wherever it went red (a red pre-step is the selection working; a chosen test red only in the gate is
the rebase or load). Pinned by tests/test_selection_miss.py.

GOH.SELECTAUDIT (the OpenClaw CI write-up's shadow comparison): the selection-miss ledger
`<landq>/selection_misses.jsonl`. On every red land attempt the lander reads the log's `FAILED
tests/...` lines against the selection the tip's OWN `tools/affected_tests.py` makes (over `git
archive`) for its diff from main: one `judged` row {source, item, sha, day, changed, selected,
failed, missed} and one `miss` row per failed test the selection did not choose (a chosen test is
never a miss, wherever it went red). The idle full run is shadowed the same way for the diff since
the previous run (`source: full`): a measure, never a skip. `--replay --since DAY` judges done/
records, main from its reflog at the enqueue (2026-11-17: 2 of 19 reds, 11 tests:
GOH.BUILDINCLUDES' 10 in test_rust_gate.py, GOH.GRACESHAPES' 1, both closed since). `goh land
--report` prints the misses per day. Invoked by `tools/landq.py`.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: nothing to run
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import datetime as dt
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitrun
import land_records  # lib/: the land records' one door (GOH.STATEDB)
import state_paths
from tmp_root import goh_tmp

FAILED = re.compile(r"^(?:\d+:)?[ \t]*FAILED (tests/[^\s@]+)", re.MULTILINE)
SELECTOR = "tools/affected_tests.py"
# A gate step's red line, the gate's failure-lines copy included (gates/local_ci.sh: `✗ [9/9]
# FAILED (exit status 1): "$GOH_PY" ./scripts/release_canary.py`, GOH.PYTESTPIN's land).
STEP_RED = re.compile(r"^(?:\d+:)?[ \t]*✗ \[\d+/\d+\] FAILED \([^)]*\): (.+?)[ \t]*$", re.MULTILINE)
# The gate steps the pre-step can run, by script, and the line its run prints in the land log
# (tools/affected_tests.py canary, lib/fast_tests.py structural). A step not here never ran there.
PRE_RAN = {
    "scripts/release_canary.py": "the release canary, as the land gate runs it",
    "gates/structural.sh": "pre-land: the structural gates over the branch first",
}


def failed_tests(text: str) -> list[str]:
    return sorted(set(FAILED.findall(text)))


def red_steps(text: str) -> list[str]:
    """The gate steps a log saw go red, by their command."""
    return sorted(set(STEP_RED.findall(text)))


def step_script(cmd: str) -> str:
    """The script a step runs: its first path-like word, `./` dropped."""
    words = [w.strip("\"'") for w in cmd.split()]
    return next((w.removeprefix("./") for w in words if "/" in w and "$" not in w), cmd)


def step_rows(base: dict, changed: list[str], steps: list[str], text: str) -> list[dict]:
    """One `step` row per red gate step: whether the pre-step ran it (its PRE_RAN line in the log).
    One it ran and that still went red is recorded with pre_ran and miss False -- the selection
    worked, as a chosen test red only in the gate does; one it never ran is the selection's miss."""
    rows = []
    for cmd in steps:
        script = step_script(cmd)
        ran = PRE_RAN.get(script, "\0") in text
        rows.append({"kind": "step", **base, "step": cmd, "script": script, "pre_ran": ran,
                     "miss": not ran, "changed": changed})  # fmt: skip
    return rows


def covered(test: str, chosen: Iterable[str]) -> bool:
    """Whether the selection ran TEST: its file whole, its node, or the class holding it."""
    node = re.sub(r"\[.*$", "", test)
    file = node.split("::")[0]
    return any(c in (file, node) or node.startswith(f"{c}::") for c in chosen)


def judge(source: str, item: str, sha: str, day: str, changed: list[str], chosen: list[str],
          failed: list[str], steps: list[str] | None = None,
          text: str = "") -> list[dict]:  # fmt: skip
    """The judged row, a `miss` row per unchosen failed test, a `step` row per red gate STEP
    (GOH.MISSLEDGERGATE: a step red failed no test, so it once wrote nothing)."""
    missed = [t for t in failed if not covered(t, chosen)]
    base = {"source": source, "item": item, "sha": sha, "day": day}
    srows = step_rows(base, changed, steps or [], text)
    head = {"kind": "judged", **base, "changed": changed, "selected": len(chosen),
            "failed": len(failed), "missed": len(missed), "steps": len(srows),
            "steps_missed": sum(r["miss"] for r in srows)}  # fmt: skip
    misses = ({"kind": "miss", **base, "test": t, "selected": len(chosen)} for t in missed)
    return [head, *misses, *srows]


def _git(repo: Path, *args: str) -> str:
    """A git READ in REPO (lib/gitrun.py: under its deadline, no inherited GIT_* answers)."""
    out = gitrun.run_reader(list(args), repo=repo, timeout=300)
    if out.returncode:
        raise subprocess.CalledProcessError(out.returncode, ["git", *args], out.stdout, out.stderr)
    return out.stdout


def changed(repo: Path, base: str, sha: str) -> list[str]:
    out = _git(repo, "diff", "--name-only", "--no-renames", f"{base}...{sha}")
    return [line for line in out.splitlines() if line]


def selection(repo: Path, sha: str, paths: list[str]) -> list[str] | None:
    """What SHA's own selector chooses for PATHS, run over `git archive SHA`; None when SHA has no
    selector (a repo whose pre-step is not tools/affected_tests.py)."""
    with tempfile.TemporaryDirectory(dir=goh_tmp(), prefix="goh-selection.") as tmp:
        tar = Path(tmp) / "tree.tar"
        _git(repo, "archive", "-o", str(tar), sha)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        tree = Path(tmp) / "tree"
        with tarfile.open(tar) as t:
            t.extractall(tree, filter="data")
        if not (tree / SELECTOR).is_file():
            return None
        env.pop("PYTHONPATH", None)  # the tip's own lib/, never the caller's
        argv = [sys.executable, str(tree / SELECTOR), "--root", str(tree), "--changed", *paths]
        out = subprocess.run(argv, capture_output=True, text=True, cwd=tree, env=env,
                             stdin=subprocess.DEVNULL, timeout=600, check=True)  # fmt: skip
        return out.stdout.split()


def append(q: Path, rows: list[dict]) -> int:
    """Append ROWS to the ledger (goh.db's selection_misses, lib/land_records.py), each (kind,
    source, item, sha, test) once; the count written."""
    have = {_key(r) for r in read(q)}
    new = [r for r in rows if _key(r) not in have]
    if new:
        land_records.append_misses(q, new)
    return len(new)


def _key(row: dict) -> tuple:
    return tuple(row.get(k) for k in ("kind", "source", "item", "sha", "test", "step"))


def read(q: Path) -> list[dict]:
    return land_records.misses(q)


def _measure(q: Path, source: str, item: str, repo: Path, base: str, sha: str,
             log: Path | list[Path], day: str) -> list[dict]:  # fmt: skip
    logs = log if isinstance(log, list) else [log]
    failed, text = _failed(logs), _text(logs)
    paths = changed(repo, _git(repo, "merge-base", base, sha).strip(), sha)
    chosen = selection(repo, sha, paths)
    if chosen is None:
        return []
    rows = judge(source, item, sha, day, paths, chosen, failed, red_steps(text), text)
    append(q, rows)
    return rows


def attempt_logs(outcome: dict) -> list[Path]:
    """The logs that judged a land attempt: its own, and every red gate run of a batch whose stack
    held it (`red_logs`, lib/landq_batch.py). A bisect's lone run of the item may fail no test --
    a gate step, a carried verdict -- where the stack's run failed them: GOH.LANDLOGPRUNE's batch
    went red on tests/test_check_lock_order.py in gate 1 only, and its own log held gate 3's
    step failure, so the ledger read no failed test and wrote nothing (GOH.SELECTLOCKORDER)."""
    named = [outcome.get("log") or "", *(outcome.get("red_logs") or [])]
    return [p for p in dict.fromkeys(Path(n) for n in named if n) if p.is_file()]


def _text(logs: list[Path]) -> str:
    return "\n".join(p.read_text(errors="replace") for p in logs)


def _failed(logs: list[Path]) -> list[str]:
    return failed_tests(_text(logs))


def _judgeable(logs: list[Path]) -> bool:
    """A failed test or a red gate step to judge."""
    text = _text(logs)
    return bool(failed_tests(text) or red_steps(text))


def _today() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")


def record_land(q: Path, outcome: dict, item: str) -> str:
    """A red land attempt's rows; never raises (the instrument never fails a land): why it could
    not judge, or ""."""
    logs = attempt_logs(outcome)
    if outcome.get("state") == "landed" or outcome.get("mode") != "land" or not logs:
        return ""
    try:
        if not _judgeable(logs):
            return ""
        repo, sha = Path(outcome["repo"]), str(outcome["sha"])
        _measure(q, "land", item, repo, "HEAD", sha, logs, _today())
    except Exception as e:  # noqa: BLE001 -- a broken instrument says so, and the land goes on
        return f"{item}: the selection-miss ledger could not judge it: {e}"
    return ""


def shadow(q: Path, repo: Path, base: str, sha: str, log: Path) -> str:
    """The idle full run's shadow: its failures against the selection for BASE..SHA."""
    if not base or base == sha:
        return ""
    try:
        _measure(q, "full", f"main@{sha[:8]}", repo, base, sha, log, _today())
    except Exception as e:  # noqa: BLE001
        return f"idle full suite: the selection shadow could not judge {sha[:8]}: {e}"
    return ""


def replay(q: Path, since: str = "") -> list[dict]:
    """Judge every red land attempt (lib/land_records.py) from SINCE (a UTC day): main from its
    reflog at the enqueue, the log the attempt left."""
    out: list[dict] = []
    for run, rec in land_records.records(q):
        ns, _, item = run.partition("-")
        day = dt.datetime.fromtimestamp(int(ns) / 1e9, dt.UTC).strftime("%Y-%m-%d")
        logs = attempt_logs(rec)
        if rec.get("state") == "landed" or rec.get("mode") != "land" or day < since:
            continue
        if not _judgeable(logs):
            continue
        base = f"main@{{{int(ns) // 10**9}}}"
        try:
            out += _measure(q, "land", item, Path(rec["repo"]), base, rec["sha"], logs, day)
        except (OSError, subprocess.SubprocessError) as e:
            print(f"{run}: not judged: {e}", file=sys.stderr)
    return out


def summary(rows: list[dict], since: str = "") -> dict[str, dict]:
    """{day: {judged, missed (judged reds with a miss: an unchosen test or a gate step the pre-step
    never ran), tests (missed tests), steps (missed steps), full (shadow runs), full_missed}}."""
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("kind") != "judged" or str(r.get("day")) < since:
            continue
        d = out.setdefault(str(r["day"]), dict.fromkeys(("judged", "missed", "tests", "steps",
                                                          "full", "full_missed"), 0))  # fmt: skip
        full = r.get("source") == "full"
        d["full" if full else "judged"] += 1
        d["full_missed" if full else "missed"] += bool(r.get("missed") or r.get("steps_missed"))
        d["tests"] += int(r.get("missed") or 0)
        d["steps"] += int(r.get("steps_missed") or 0)
    return dict(sorted(out.items()))


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="replay the selection-miss ledger from done/")
    ap.add_argument("--replay", action="store_true", required=True)
    ap.add_argument("--since", default="")
    ap.add_argument("--queue", default=str(state_paths.landq_dir()))
    args = ap.parse_args(argv)
    for r in replay(Path(args.queue), args.since):
        print(json.dumps({k: v for k, v in r.items() if k != "changed"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
