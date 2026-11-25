"""lib/flakes.py -- every pass-on-retry is kept: one flake register, and a test that passes on a
retry twice in 14 days files its item (GOH.FLAKEREGISTER).

THE COST. 91 fault lines from 71 agents said a test "passed when run again" or "failed once, then
3/3 alone". The retry greened the run and nothing was kept, and GOH.BACKSTOPSTAMP (53eee0ba)
showed one such "load flake" was a real regression (proc_pidinfo against ps). A flake nobody
counts is a defect nobody files.

THE RETRY SITES, each calling `record` when the first run was red and the retry green:
    lib/reuse_audit.py compare   a sampled reuse whose fresh run went red is run again: the proven
                                 step's shadow (gates/local_ci.sh `_again` through
                                 gates/_proven_audit.sh), the pre-land set's shadow
                                 (lib/affected_verdict.py), the release push's `landed` sample
                                 (lib/push_runs.sh push_release_shadow, push_full_gate_again)
    tools/landq.py record        a landing whose item went red on the SAME enqueued commit before:
                                 the re-enqueue was the retry, of the enqueue's pre-step or of the
                                 land gate (`land_retry`; the red's tests by lib/land_cause.py)
No other goh runner retries a test: tools/pytest.sh loads no rerun plugin and goh passes nextest
no `--retries` (nor declares a nextest profile with retries).

THE REGISTER is goh.db's `flakes` table (lib/small_stores.py, GOH.STATEDBS3), in the goh.db beside
$GOH_FLAKE_DIR (default ~/.local/state/goh/flakes, under XDG_STATE_HOME when set), whose
register.jsonl and by-test/<key>.jsonl it was (read until their import):
    a record              one row per pass-on-retry: {id, test, repo, sha, load, first, second,
                          log: {first, second}, site, at, epoch}
    a filing              {filed, epoch} when the test's item was filed, a row of that test
`key` is sha1(test id)[:8]; a record's id is `<key>.<n>`. The 2-in-14-days rule reads only the
test's own rows (the index on test): a record costs one insert and one small read. Records
since the test's last filing that are at most DAYS old, two or more, file `GOH.FLAKE<KEY>`
through `loopctl file --json` (lib/reuse_fault.py `_file`: the GOH_LOOPCTL and GOH_LOOP_QUEUE
seams) carrying them, and the filing is marked, so one test files one item. `goh flakes` lists
the register. Pinned by tests/test_flakes.py.

`goh flakes [--days N] [--json]`, the flake register (GOH.FLAKEREGISTER): every pass-on-retry -- a
sampled reuse whose red fresh run went green again (`reuse_audit.py compare`: the proven step
shadow, the pre-land set shadow, the release push sample) and a landing whose item went red on the
same enqueued commit (`tools/landq.py`) -- is one appended record (test, repo, sha, load, both
outcomes, logs) in `~/.local/state/goh/flakes/register.jsonl` (`GOH_FLAKE_DIR`); a test with two
records in 14 days files `GOH.FLAKE<key>` once through `loopctl file`, carrying them, and the
lander names the register entry. Invoked by `tools/landq.py`.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lock_order  # the one acquire path
import small_stores  # goh.db's flakes table (GOH.STATEDBS3)

DAYS = 14
FILE_AT = 2  # records in DAYS that file the test's item
DUP_EXAMPLE = re.compile(r'"not_dup_of": (\{.*\})')
TRIES = 4  # dup refusals answered for other tests' flake items
UNNAMED = "land gate of {repo} (no test named in its log)"


def root() -> Path:
    if seam := os.environ.get("GOH_FLAKE_DIR"):
        return Path(seam)
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(state) / "goh" / small_stores.FLAKE_DIR


def register() -> Path:
    return root() / "register.jsonl"


def key(test: str) -> str:
    return hashlib.sha1(test.encode(), usedforsecurity=False).hexdigest()[:8]


def item_id(test: str) -> str:
    return f"GOH.FLAKE{key(test).upper()}"


def _lines(path: Path) -> list[dict]:
    out = []
    with contextlib.suppress(OSError):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            with contextlib.suppress(ValueError):
                if isinstance(row := json.loads(line), dict):
                    out.append(row)
    return out


@contextlib.contextmanager
def _test_lock(k: str) -> Iterator[None]:
    """The test's read-decide-file held alone against a concurrent record: the filing runs
    loopctl, which no database transaction spans."""
    path = root() / "by-test" / f".{k}.flake.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        lock_order.flock(fh, "flake_test")  # released when the file closes
        yield


def record(test: str, repo: str, sha: str, site: str, logs: tuple[str, str] = ("", ""),
           load: float | None = None, first: str = "red",
           second: str = "green") -> dict:  # fmt: skip
    """Keep one pass-on-retry of TEST; the record as written, with `filed` naming its item when
    this record made two in DAYS. Never raises: the instrument is no reason a run fails."""
    try:
        return _record(test, repo, sha, site, logs, load, first, second)
    except Exception as e:  # noqa: BLE001 -- the instrument is never the reason a gate fails
        return {"test": test, "error": f"not kept: {e}"}


def _record(test: str, repo: str, sha: str, site: str, logs: tuple[str, str],
            load: float | None, first: str, second: str) -> dict:  # fmt: skip
    if load is None:
        with contextlib.suppress(OSError):
            load = round(os.getloadavg()[0], 2)
    now = time.time()
    k = key(test)
    with _test_lock(k):
        rows = small_stores.flakes(root(), test)
        n = sum(1 for r in rows if "id" in r) + 1
        rec = {"id": f"{k}.{n}", "test": test, "repo": repo, "sha": sha, "load": load,
               "first": first, "second": second, "log": {"first": logs[0], "second": logs[1]},
               "site": site, "at": dt.datetime.fromtimestamp(now, dt.UTC).isoformat(
                   timespec="seconds"), "epoch": round(now, 3)}  # fmt: skip
        small_stores.flake_add(root(), test, rec)
        since = 0
        for i, r in enumerate(rows):
            if "filed" in r:
                since = i + 1
        recent = [r for r in rows[since:] if "id" in r and now - r.get("epoch", 0) <= DAYS * 86400]
        if len(recent) + 1 >= FILE_AT:
            said = _file(test, [*_by_ids({r["id"] for r in recent}), rec])
            rec["filed"] = said
            if said.startswith(("filed ", "already filed ")):
                small_stores.flake_add(
                    root(), test, {"filed": item_id(test), "epoch": rec["epoch"]}
                )
    return rec


def _by_ids(ids: set[str]) -> list[dict]:
    """The register's records named IDS (read only when an item is filed, never per record)."""
    return [r for r in small_stores.flakes(root()) if r.get("id") in ids]


def _log(rec: dict, which: str) -> str:
    return str((rec.get("log") or {}).get(which) or "-")


def _item(test: str, recs: list[dict]) -> dict:
    iid = item_id(test)
    repo = str(recs[-1].get("repo") or "").removesuffix(
        "/.git"
    )  # a reuse key's repo is its git dir
    root_dir = Path(os.environ.get("LOOPCTL_ROOT") or Path.home() / "Projects")
    try:  # the loop's repo token: a path under ~/Projects, else the checkout's name
        repo = str(Path(repo).resolve().relative_to(root_dir.resolve()))
    except (ValueError, OSError):
        repo = Path(repo).name
    named = "; ".join(f"{r['id']} {r.get('site')} at {str(r.get('sha'))[:12]}, load "
                      f"{r.get('load')}, {r.get('first')} then {r.get('second')}, logs "
                      f"{_log(r, 'first')} / {_log(r, 'second')}"
                      for r in recs)  # fmt: skip
    return {"id": iid, "repo": repo or "gates_of_heck", "status": "todo", "phase": "SPD",
            "regression": True, "depends": [],
            "source": f"goh flake register (lib/flakes.py), {register()}",
            "item": f"{test} went red and passed on a retry {len(recs)} times in {DAYS} days "
            f"(the flake register, `goh flakes`): {named}. A pass on retry is a defect until its "
            "cause is named: a real race, a load-sensitive bound, or a regression the retry hid "
            "(GOH.BACKSTOPSTAMP).",
            "flake_records": recs,
            "metric": f"flake records of {test} in the register per {DAYS} days",
            "predicted": f"{len(recs)} -> 0: the cause found and fixed, no record after the land",
            "done_state": f"the cause of {test}'s red named, a test that reproduces it red, the "
            "fix landed, and no new flake record of it in the register",
            "class_of": "none",
            "class_why": f"one test's own flake ({test}), found from its records; its cause is "
            "that "
            "test's or its subject's"}  # fmt: skip


def _file(test: str, recs: list[dict]) -> str:
    loopctl = os.environ.get("GOH_LOOPCTL") or str(Path.home() / "Projects/.loop/tools/loopctl.py")
    iid = item_id(test)
    if not os.path.isfile(loopctl):
        return f"not filed: no loopctl at {loopctl}"
    import reuse_fault  # lib/: `loopctl file --json` under GOH_LOOP_QUEUE (one funnel)

    fields = _item(test, recs)
    code, said = 1, ""
    for _ in range(TRIES):
        code, said = reuse_fault._file(loopctl, fields)
        found = DUP_EXAMPLE.search(said) if code else None
        others = json.loads(found.group(1)) if found else {}
        if not others or not all(str(o).startswith("GOH.FLAKE") for o in others):
            break  # another test's flake item is no repeat of this one; anything else is said
        fields.setdefault("not_dup_of", {}).update(
            dict.fromkeys(others, f"another test's flake; this one is {test}")
        )
    if code == 0:
        return f"filed {iid}"
    if f"{iid} already exists" in said:
        return f"already filed {iid}"
    return f"not filed: {said[-300:]}"


def recorded(test: str) -> list[dict]:
    """TEST's by-test lines (cheap: one small file), for a red land naming its earlier flakes."""
    return [r for r in small_stores.flakes(root(), test) if "id" in r]


def _failing(red: dict) -> list[str]:
    """The tests a red land record names: its `failing:` list, else its log's FAILED lines."""
    if found := re.search(r"failing: ([^)]+)\)", str(red.get("why") or "")):
        return [t.strip() for t in found.group(1).split(",") if t.strip()]
    import land_cause  # lib/: the FAILED node ids of a land log

    with contextlib.suppress(OSError, TypeError):
        return land_cause.failed_tests(Path(red["log"]).read_text(errors="replace"))
    return []


def land_retry(recs: list[tuple[str, dict]], item: str, outcome: dict) -> dict:
    """At a landing that was not its item's first attempt: each red of ITEM since its last landing
    on the SAME enqueued commit was a pass on retry; one record per test it named (UNNAMED when
    its log names none). `{"flakes": [{entry, test, filed}]}`, or {} with none. Never raises.
    RECS are the queue's land records, oldest first (lib/land_records.py, read by the lander: this
    module is loaded before every test, so it never imports the goh.db chain itself)."""
    tip = outcome.get("enqueued") or outcome.get("sha") or ""
    if outcome.get("state") != "landed" or outcome.get("mode", "land") != "land" \
            or int(outcome.get("attempt") or 1) < 2 or not tip:  # fmt: skip
        return {}
    reds: list[dict] = []
    for run, rec in recs:
        if str(rec.get("item") or run.partition("-")[2]) != item:
            continue
        reds = [] if rec.get("state") == "landed" else [*reds, rec]
    out = []
    for red in reds:
        if red.get("state") != "red" or red.get("sha") != tip:
            continue
        repo = str(outcome.get("repo") or "")
        load = red.get("load_end") if isinstance(red.get("load_end"), (int, float)) else None
        logs = (str(red.get("log") or ""), str(outcome.get("log") or ""))
        for test in _failing(red) or [UNNAMED.format(repo=Path(repo).name)]:
            rec = record(test, repo, tip, "land re-enqueue (tools/landq.py)", logs, load)
            out.append({"entry": rec.get("id") or rec.get("error"), "test": test,
                        "filed": rec.get("filed", "")})  # fmt: skip
    return {"flakes": out} if out else {}


def said(outcome: dict) -> list[str]:
    """The lander's lines about flakes: a landing's passes on retry by register entry, a red's
    failing tests that have records already. Never the words "looks flaky": an entry, or nothing."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tui.lib import counted

    lines = []
    for f in outcome.get("flakes") or []:
        filed = f" -- {f['filed']}" if f.get("filed") else ""
        lines.append(f"passed on retry: {f['test']} -- flake register entry {f['entry']} "
                     f"({register()}; `goh flakes`){filed}")  # fmt: skip
    if outcome.get("state") == "red" and outcome.get("mode", "land") == "land":
        for test in _failing(outcome):
            if recs := recorded(test):
                n = counted(len(recs), "flake record")
                lines.append(f"{test} has {n}, the last {recs[-1]['id']} (`goh flakes`): "
                             "if it passes on a re-enqueue, that is one more")  # fmt: skip
    return lines


def main(argv: list[str]) -> int:
    """`goh flakes [--days N] [--json]`: the register, one block per test, newest last."""
    import argparse

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tui.lib import counted, info, ok

    ap = argparse.ArgumentParser(prog="goh flakes", description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=float, default=None, help="only records this many days old")
    ap.add_argument("--json", action="store_true", help="the records, one JSON object per line")
    a = ap.parse_args(argv)
    now = time.time()
    recs = [r for r in small_stores.flakes(root()) if "test" in r
            and (a.days is None or now - float(r.get("epoch") or 0) <= a.days * 86400)]  # fmt: skip
    if a.json:
        for r in recs:
            print(json.dumps(r, sort_keys=True))
        return 0
    if not recs:
        ok(f"flakes: no pass-on-retry in {register()}")
        return 0
    by: dict[str, list[dict]] = {}
    for r in recs:
        by.setdefault(r["test"], []).append(r)
    info(
        f"flakes: {counted(len(recs), 'pass-on-retry record')} of "
        f"{counted(len(by), 'test')} in {register()}"
    )
    for test, rs in sorted(by.items(), key=lambda kv: kv[1][-1].get("epoch") or 0):
        n14 = sum(1 for r in rs if now - float(r.get("epoch") or 0) <= DAYS * 86400)
        filed = next((r["filed"] for r in reversed(rs) if r.get("filed")), "")
        print(f"{test}  ({len(rs)} recorded, {n14} in {DAYS} days{', ' + filed if filed else ''})")
        for r in rs:
            log = r.get("log") or {}
            print(f"  {r['id']}  {r.get('at')}  {r.get('site')}  {str(r.get('sha'))[:12]}  load "
                  f"{r.get('load')}  {r.get('first')} -> {r.get('second')}  "
                  f"{log.get('first') or '-'}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
