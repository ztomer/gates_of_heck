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
    "land records": {  # S1 goh.db 0002 lands: what is left is the files' window, until wave 3
        # the door's file half: done/ files from before the switch are read, and still written for
        # .loop's readers and old HEAD exports, until wave 3's delete commit removes it
        "lib/land_records.py",
        "lib/state_bounds.py",  # the done/ files' 14-day bound, while the door still writes them
        "lib/state_files.py",  # guards.jsonl's bound: the pre-switch file, until wave 3
    },
    "selection misses": {  # S1 goh.db 0002 selection_misses
        "lib/state_files.py",  # the pre-switch ledger file's bound, until wave 3
    },
    "ship records": {  # S2 goh.db 0003 ships: moved (lib/ship_records.py, GOH.STATEDBS2)
        "lib/ship_records.py",  # LEFT: ship_dir(), where the step logs stay (handover plan (e))
        "lib/state_bounds.py",  # LEFT: ship/* prunes the step logs and the window's record files
    },
    "ship pins": set(),  # S2 goh.db 0003 ship_pins: moved (lib/ship_records.py, GOH.STATEDBS2)
    "small stores": {  # S3 goh.db 0004 kv (the estate state and pin ledgers: lib/small_stores.py)
        "lib/cache_evict.py",
    },
    "commit gated": {  # S3 goh.db 0004 commit_gated
        "lib/commit_stamp.py",
    },
    "flakes": {  # S3 goh.db 0004 flakes
        "lib/flakes.py",
        "lib/state_files.py",
    },
    "hook ledgers": {  # S4 goh.db 0005
        "hooks/claude/agent_guard.py",
        "hooks/claude/lane_room.py",
        "scripts/model_choice_watch.py",
        "scripts/spend_watch.py",
    },
    "durations": set(),  # DUR goh.db 0006 readings: moved (lib/durations.py, GOH.DURATIONSDB)
    "reuse records": {  # V1 goh-cache.db 0002 verdicts
        "lib/reuse.py",
        "lib/state_bounds.py",
        "lib/state_files.py",
    },
    "reuse audit": {  # V1 goh.db 0007a
        "lib/reuse_audit.py",
        "lib/reuse_fault.py",
        "lib/state_bounds.py",
        "lib/state_files.py",
    },
    "kept stores": {  # V1 goh-cache.db 0002 kept
        "tests/_parse_findings.py",
        "tests/_self_walk.py",
        "tests/_tree_lint.py",
    },
    "identity memo": {  # V1 goh-cache.db 0002 memo
        "lib/identity_cache.py",
    },
    "cpu idle": {  # S3 goh-cache.db kv
        "lib/cpu_window.py",
    },
    "land queue": {  # LANDQ goh.db 0007 requests
        "lib/landq_batch.py",
        "lib/landq_early.py",
        "lib/landq_look.py",
        "lib/landq_window.py",
        "lib/state_bounds.py",
        "tools/landq.py",
    },
    "shell verdicts": {  # V2 goh-cache.db 0003 shell_verdicts
        "crates/goh/src/verdict_cache.rs",
        "lib/reuse.py",
        "tests/_tree_lint.py",
    },
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
