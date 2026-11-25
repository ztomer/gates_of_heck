"""lib/kv_fill.py -- `goh state backfill`, the kv documents: each one's parts, put into the rows
they became (GOH.KVBLOBS).

    run()    {table: the rows it holds}: goh.db's five (lib/kv_rows.py), goh-cache.db's four
             (lib/cache_rows.py); it may be killed anywhere and run again, and adds nothing twice
    left()   one line per document with a part no row holds

THE DOCUMENTS STAY until the contract, but one that only a cache needs is gone with its next write
(the canaries' memo_records, the evictor's objs, the watch's fault_candidates); agent-verify.json is
removed here, once its rows read back equal. Pinned by tests/test_cache_rows.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cache_evict  # lib/, on the path from the line above
import cache_rows
import canary_memo
import estate_registry
import goh_db
import hook_ledgers
import kv_rows
import remote_rates
import small_stores

VERIFIED = "agent-verify.json"


def _memos() -> dict[str, dict[str, dict]]:
    """{entry: the records the estate state still holds of it}."""
    state = estate_registry.read_state(estate_registry.state_file())
    out = {}
    for entry in canary_memo.KINDS:
        rec = state.get(entry)
        found = rec.get("memo_records") if isinstance(rec, dict) else None
        out[entry] = found if isinstance(found, dict) else {}
    return out


def _objs() -> dict[str, dict]:
    doc = small_stores.get(cache_evict.state_path())
    found = doc.get("objs") if isinstance(doc, dict) else None
    return found if isinstance(found, dict) else {}


def _verified() -> Path:
    return hook_ledgers.cache_dir() / VERIFIED


def run() -> dict[str, int]:
    with goh_db.transaction() as con:
        done = kv_rows.fill(con)
    remote_rates.fill()
    for entry, found in _memos().items():
        if found:
            canary_memo.keep(estate_registry.state_file(), entry, {"memo_records": found})
    ledger = cache_evict.state_path()
    if found := _objs():
        cache_rows.put_objs(ledger, cache_rows.newer(cache_rows.objs(ledger), found))
    cache_rows.take_verified(_verified())
    done["remote_secs"] = len(cache_rows.secs())
    done["canary_memo"] = sum(len(cache_rows.memo(estate_registry.state_file(), k))
                              for k in canary_memo.KINDS.values())  # fmt: skip
    done["evict_objs"] = len(cache_rows.objs(ledger))
    done["verified_shas"] = sum(map(len, cache_rows.verified().values()))
    return done


def left() -> list[str]:
    with goh_db.known() as con:
        out = kv_rows.left(con)
    if remote_rates.unfilled():
        out.append(f"duration_kv {remote_rates.RATES}: not in remote_secs")
    for entry, found in _memos().items():
        rows = cache_rows.memo(estate_registry.state_file(), canary_memo.KINDS[entry])
        live = [k for k, v in found.items() if canary_memo.live(v) and k not in rows]
        out += [f"kv estate_audit: {len(live)} of {entry}'s memo not in canary_memo"] * bool(live)
    gone = _objs().keys() - cache_rows.objs(cache_evict.state_path()).keys()
    out += [f"kv cache-evict: {len(gone)} objects not in evict_objs"] * bool(gone)
    return out + [f"{VERIFIED}: still there"] * _verified().exists()
