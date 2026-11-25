"""NO TEST FILE CHURNS THE DISK (GOH.FSEVQUIET): each file a run ran is judged by its worst test.

Owner, 2026-11-25: "let's also make the no disk churn a rule so it won't regress once we fix it".
The reading of a file is its WORST test's file events (the records under that test's own tmp_path,
lib/fs_events.py `per_dir`) and bytes left written (tests/_fs_bytes.py), so a file reads the same in
a land's partial run as in a whole one and is judged in both. tests/fs_events_ceiling.json holds:

  new     a file of neither list below: what any new test file is held to
  today   a file of tests/fs_churn_today.txt, which stood before the rule; the list only shrinks
  named   a file's own ceilings, above those: the exceptions, which only shrink

A list names no file that is gone (tests/test_fs_ceilings.py), and a whole run whose reading of a
named file is under half of what it would be held to without its name is RED: the name is stale.
A window whose events were dropped is no reading of events (tests/_fs_churn.py), and never a pass:
it says unmeasured; bytes are read from the files themselves and judged all the same.

    python3 tests/_fs_ceilings.py REPORT   (tools/pytest.sh keeps the last at ~/.cache/goh/)

takes from fs_churn_today.txt each file a WHOLE measured run read at half of `new` or less.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TODAY = Path(__file__).resolve().parent / "fs_churn_today.txt"
KINDS = ("events", "bytes")


def today() -> set[str]:
    lines = TODAY.read_text(encoding="utf-8").splitlines()
    return {ln for ln in lines if ln and not ln.startswith("#")}


def by_test(per_dir: dict[str, int], dirs: dict[str, str]) -> dict[str, int]:
    """{node id: events} of {tmp_path key: events} and {tmp_path key: node id}."""
    out: dict[str, int] = {}
    for key, n in per_dir.items():
        if node := dirs.get(key):
            out[node] = out.get(node, 0) + n
    return out


def readings(events: dict[str, int], size: dict[str, int], ran: set[str]) -> dict[str, dict]:
    """{file: {events, bytes, and each one's worst test}} of the files of `ran` (node ids)."""
    out: dict[str, dict] = {
        f: {"events": 0, "bytes": 0} for f in {node.split("::")[0] for node in ran}
    }
    for kind, per in (("events", events), ("bytes", size)):
        for node, n in per.items():
            row = out.setdefault(node.split("::")[0], {"events": 0, "bytes": 0})
            if n > row[kind]:
                row[kind], row[f"{kind}_test"] = n, node
    return out


def without_a_name(file: str, conf: dict, before: set[str]) -> dict[str, int]:
    return conf["today" if file in before else "new"]


def faults(
    files: dict[str, dict], conf: dict, before: set[str], whole: bool, measured: bool
) -> list[str]:
    """Each file over its ceiling, and (a whole, measured run) each name no longer needed."""
    out = []
    kinds = KINDS if measured else ("bytes",)
    for file, row in sorted(files.items()):
        base = without_a_name(file, conf, before)
        own = conf["named"].get(file, {})
        for kind in kinds:
            limit = own.get(kind, base[kind])
            if row[kind] > limit:
                out.append(
                    f"{row.get(f'{kind}_test', file)} made {row[kind]} {kind}, over {file}'s"
                    f" ceiling {limit}"
                )
        if own and whole and measured and all(2 * row[k] <= base[k] for k in KINDS):
            out.append(
                f"{file} is named for {own} and read {row['events']} events and {row['bytes']}"
                f" bytes, under half of {base}: remove its name"
            )
    return out


def settled(rep: dict, new: dict[str, int], before: set[str]) -> set[str]:
    """The files of `before` that a whole measured run read at half of `new` or less."""
    if not rep.get("whole") or rep.get("unmeasured"):
        return set()
    files = rep["files"]
    return {f for f in before if f in files and all(2 * files[f][k] <= new[k] for k in KINDS)}


if __name__ == "__main__":
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    conf = json.loads((TODAY.parent / "fs_events_ceiling.json").read_text(encoding="utf-8"))
    done = settled(report, conf["new"], today())
    kept = [ln for ln in TODAY.read_text(encoding="utf-8").splitlines() if ln not in done]
    TODAY.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print(f"{len(done)} files held to `new` now, {len(kept) - 1} left in {TODAY.name}")
