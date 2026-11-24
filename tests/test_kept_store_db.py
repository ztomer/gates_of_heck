"""The kept verdict rows are hermetic, merged into the real file once (GOH.VERDICTDB V1).

tests/_kept_store.py kept the parse findings as files in the machine's REAL goh state, written by
every xdist worker of every suite (the handover plan's problem 6: tests deliberately wrote the real
stores; parse-findings held 257 entries with no bound in `goh state`). They are rows of goh-cache.db
now: a worker writes only the suite's own file, and the controller seeds it from the real file at
the session's start and merges the session's rows back at its end, once, by INSERT OR IGNORE, with
rows past STORE_DAYS dropped.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys

import _kept_store as ks
import _parse_findings as pf
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience


def _rows(db) -> list[tuple]:
    with sqlite3.connect(db) as con:
        out = con.execute("SELECT store, key, body FROM kept ORDER BY key").fetchall()
    con.close()
    return out


def test_parse_findings_are_kept_rows_in_the_named_goh_cache_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(pf.CACHE_ENV, str(tmp_path / "cache"))
    assert pf._store() == tmp_path / "cache" / "goh-cache.db"
    ks.put(pf.STORE, {"k1": '["a.sh:1:1: x"]'}, pf._store())
    assert ks.get(pf.STORE, ["k1", "k2"], pf._store()) == {"k1": '["a.sh:1:1: x"]'}
    assert not list((tmp_path / "cache").glob("*/*.json"))  # no record file


def test_a_merge_adds_only_what_the_target_lacks_and_drops_the_month_old(tmp_path) -> None:
    suite, real = tmp_path / "suite.db", tmp_path / "real.db"
    ks.put("parse-findings", {"old": "1", "both": "suite"}, suite)
    ks.put("parse-findings", {"both": "real"}, real)
    with sqlite3.connect(suite) as con:
        con.execute("UPDATE kept SET at = at - 31 * 86400 WHERE key='old'")
    con.close()
    ks.put("parse-findings", {"new": "2"}, suite)
    assert ks.merge(suite, real, False) == 1  # `new` alone: `old` is past the month
    assert _rows(real) == [("parse-findings", "both", "real"), ("parse-findings", "new", "2")]


def test_only_the_top_level_controller_merges_and_no_child_inherits_the_name(tmp_path) -> None:
    """The real file's name leaves the environment when the plugin loads: a session a test starts
    (a worker, a fixture pytest) has none, so it can neither seed from nor write the real file."""
    assert ks.KEPT_ENV not in os.environ
    code = "import os, sys; sys.path.insert(0, 'tests'); import _kept_store as k; print(k._REAL)"
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True,
                         text=True, check=True, stdin=subprocess.DEVNULL, timeout=patience(60),
                         env=hermetic_env(**{ks.KEPT_ENV: str(tmp_path / "r.db")}))  # fmt: skip
    assert out.stdout.strip() == str(tmp_path / "r.db")  # read once, at import


def test_goh_state_names_no_kept_store_as_unbounded(tmp_path) -> None:
    """GOH.VERDICTBOUND: `goh state` said "parse-findings: 257 entries and no bound" and the same of
    tree-lint-verdicts; each kept store is registered with its bound."""
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    import state_bounds

    for name in ("parse-findings", "tree-lint-verdicts", "self-walk-verdicts"):
        for n in range(state_bounds.GROWTH + 1):
            (tmp_path / name / f"{n:02x}").mkdir(parents=True)
    assert state_bounds.unbounded(tmp_path) == []
