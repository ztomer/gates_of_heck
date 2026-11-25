"""goh's state stores have one door each (GOH.DBSEAM): goh.db through lib/goh_db.py, goh-cache.db
through lib/goh_cache_db.py. The stores the database plan moves (handover plan (b): land and ship
records, the small stores, the hook ledgers, the duration table, the reuse and kept verdict stores,
the land queue, the shell-lint verdicts) are each named today by the files below; a lane that moves
a store deletes its files' entries, and the plan is done when ALLOWED is empty (close-the-class).

The gate: every file outside the seam that names a listed store (tests/_state_seam_scan.py: a path
segment joined into a path, a literal path, a store file in a shell line, the Rust verdict store)
must be in ALLOWED under that store; an entry that no longer names it fails too (a ratchet: a moved
reader's entry goes with it).
"""

from __future__ import annotations

from pathlib import Path

import _state_seam_scan as scan
from conftest import REPO_ROOT

# store -> the files that name it today (2026-11-23), each removed by the lane that moves the store
ALLOWED: dict[str, set[str]] = {
    "land records": set(),  # S1 goh.db 0002 lands: moved; the files retired (state_retire_lands)
    "selection misses": set(),  # S1 goh.db 0002 selection_misses: moved (state_retire_lands)
    "ship records": {  # S2 goh.db 0003 ships: moved; the files retired (lib/state_retire_ships.py)
        "lib/ship_records.py",  # LEFT: ship_dir(), where the step logs stay (handover plan (e))
        "lib/state_bounds.py",  # LEFT: ship/* bounds the step logs (handover plan (e))
    },
    "ship pins": set(),  # S2 goh.db 0003 ship_pins: moved; the files retired (state_retire_ships)
    "push sweeps": {  # goh.db 0022: moved (lib/push_sweep_rows.py); the files retired
        "lib/pin_sweep.py",  # LEFT: sweep_path(), the name every record has for a sweep, no file
    },
    "small stores": set(),  # S3 goh.db 0004 kv: moved (lib/small_stores.py, GOH.STATEDBS3)
    "commit gated": set(),  # S3 goh.db 0004 commit_gated: moved (lib/small_stores.py)
    "flakes": set(),  # S3 goh.db 0004 flakes: moved; the files retired (lib/state_retire.py)
    "hook ledgers": set(),  # S4 goh.db 0005: moved; the files retired (lib/state_retire.py)
    "durations": set(),  # DUR goh.db 0006 readings: moved (lib/durations.py, GOH.DURATIONSDB)
    "reuse records": {  # V1 goh-cache.db 0002 verdicts: moved; the files retired (GOH.VERDICTDB,
        # lib/state_retire_verdicts.py). LEFT, each naming the reuse ROOT, not a record file: the
        # root stays for the files kept there by decision (profstore, profreach, binreach;
        # handover plan (e), tests/test_reuse_root_files.py)
        "lib/reuse_store.py",  # root(): GOH_REUSE_DIR, else <state>/goh/reuse
        "lib/state_bounds.py",  # the root's Container and its decided directories' rows
        "lib/state_files.py",  # reuse/profstore/ledger.jsonl, bounded at its writer
    },
    "reuse audit": set(),  # V1 goh.db 0008 reuse_audit: moved; the files retired (wave 3)
    "kept stores": set(),  # V1/V2 goh-cache.db kept + shell_verdicts rows (tests/_kept_store.py)
    "identity memo": set(),  # V1 goh-cache.db 0002 memo: moved (lib/identity_cache.py)
    "cpu idle": set(),  # S3 goh-cache.db kv: moved (lib/cpu_window.py, GOH.STATEDBS3)
    "land queue": set(),  # LANDQ goh.db 0007 requests: moved (lib/landq_state.py, GOH.LANDQDB); an
    # older goh's files retired (lib/state_retire_landq.py)
    "shell verdicts": set(),  # V2 goh-cache.db 0003 shell_verdicts: moved (verdict_cache.rs)
}


def violations(root: Path, allowed: dict[str, set[str]]) -> list[str]:
    found = scan.scan(root)
    seen = {(f, store) for f, store, _, _ in found}
    bad = [f"{f}:{line}: {lit!r} names the {store} store outside the seam ({scan.STORES[store][0]})"
           for f, store, line, lit in found if f not in allowed.get(store, set())]  # fmt: skip
    bad += [f"ALLOWED[{store!r}] names {f}, which no longer names it: delete the entry"
            for store, files in allowed.items() for f in sorted(files)
            if (f, store) not in seen]  # fmt: skip
    return bad


def test_every_store_reader_is_on_the_ratchet() -> None:
    assert violations(REPO_ROOT, ALLOWED) == []


def test_every_listed_store_is_a_store_of_the_plan() -> None:
    assert set(ALLOWED) == set(scan.STORES), set(ALLOWED) ^ set(scan.STORES)


def test_a_planted_reader_is_refused(tmp_path) -> None:
    """The gate's own red: a new lib module that opens the land records itself, a hook that appends
    to agent-spawns.jsonl, a shell line naming the duration table and a Rust verdict path; a bare
    "done" status and a shell FIFO named done are not stores."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "planted.py").write_text(
        "import os\nfrom pathlib import Path\n\n"
        "recs = sorted((Path('q') / 'done').glob('*.json'))\n"
        "spawns = os.path.join('/s', 'agent-spawns.jsonl')\n"
        "state = 'done'\n"
    )
    (tmp_path / "lib" / "planted.sh").write_text(
        '#!/bin/bash\ncat "$STATE/test-durations.json"\nmkfifo "$DIR/done"\n'
    )
    (tmp_path / "crates" / "x" / "src").mkdir(parents=True)
    (tmp_path / "crates" / "x" / "src" / "lib.rs").write_text('let p = home("goh/verdicts");\n')
    got = violations(tmp_path, {})
    assert sorted((v.split(":")[0], v.split(":")[1]) for v in got) == [
        ("crates/x/src/lib.rs", "1"),
        ("lib/planted.py", "4"),
        ("lib/planted.py", "5"),
        ("lib/planted.sh", "2"),
    ], got


def test_a_stale_entry_fails(tmp_path) -> None:
    (tmp_path / "lib").mkdir()
    got = violations(tmp_path, {"flakes": {"lib/gone.py"}})
    assert got == [
        "ALLOWED['flakes'] names lib/gone.py, which no longer names it: delete the entry"
    ]
