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
    assert os.environ.get(ks.KEPT_ENV) is None
    code = "import os, sys; sys.path.insert(0, 'tests'); import _kept_store as k; print(k._REAL)"
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True,
                         text=True, check=True, stdin=subprocess.DEVNULL, timeout=patience(60),
                         env=hermetic_env(**{ks.KEPT_ENV: str(tmp_path / "r.db")}))  # fmt: skip
    assert out.stdout.strip() == str(tmp_path / "r.db")  # read once, at import


def test_a_retired_kept_store_left_on_disk_is_named_never_excused(tmp_path) -> None:
    """GOH.VERDICTBOUND registered the kept stores' directories while they were files; they are rows
    now and their files retired (`goh state retire parse-findings shell-verdicts`), so a directory
    left behind is named by `goh state` until it is, never excused by a stale registration."""
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    import state_bounds

    names = ("parse-findings", "self-walk-verdicts", "tree-lint-verdicts")
    for name in names:
        for n in range(state_bounds.GROWTH + 1):
            (tmp_path / name / f"{n:02x}").mkdir(parents=True)
    got = state_bounds.unbounded(tmp_path)
    assert sorted(line.split(":")[0] for line in got) == list(names), got


def test_a_lookup_counts_the_keys_it_missed(tmp_path) -> None:
    """The drift guard reads `missed` around each phase: a phase that missed is a cold reading,
    never a test's growth (lib/long_pole.py `settle`, GOH.LONGPOLECOLD)."""
    db = tmp_path / "goh-cache.db"
    ks.put(pf.STORE, {"k1": "[]"}, db)
    before = ks.missed()
    ks.get(pf.STORE, ["k1", "k2", "k3"], db)
    assert ks.missed() - before == 2
    ks.get(pf.STORE, ["k1"], db)
    assert ks.missed() - before == 2, "a hit is no miss"
    ks.get(pf.STORE, ["k1"], tmp_path / "absent.db")
    assert ks.missed() - before == 3, "no file: every key missed"


def test_a_refused_merge_into_the_real_file_says_the_next_session_runs_cold(
    tmp_path, monkeypatch, capsys
) -> None:
    """A lane copy shipping a goh-cache.db migration the real file lacks may not write it
    (lib/db_record.py): the session's kept rows were dropped without a word, and the next run of
    the shell-lint tests started cold and read as 72 s of growth (GOH.LONGPOLECOLD). The refusal
    is said, with what was lost."""
    suite, real = tmp_path / "suite.db", tmp_path / "real.db"
    ks.put(pf.STORE, {"a": "1", "b": "2"}, suite)
    ks.put(pf.STORE, {"a": "1"}, real)
    gcd = ks._cache()

    def refused(at=None, named=False):
        raise gcd.Refused(f"{at}: lacks 0003 and this copy is not the code of record")

    monkeypatch.setattr(gcd, "transaction", refused)
    assert ks.merge(suite, real, True) == 0
    said = capsys.readouterr().err
    assert "1 kept row" in said and "cold" in said and str(real) in said, said
    assert _rows(real) == [(pf.STORE, "a", "1")]


def test_rows_any_process_wrote_since_this_one_began_count_as_misses(tmp_path, monkeypatch) -> None:
    """GOH.COLDRUSTMISS: the binary's shell-verdict misses are the rows it writes back; `written`
    counts them in the kept file, every kept table, and none while there is no file or table."""
    db = tmp_path / "goh-cache.db"
    monkeypatch.setenv("GOH_CACHE_DB", str(db))
    monkeypatch.setattr(ks, "_WRITTEN", {"seen": None, "rows": 0})
    assert ks.written() == 0, "no file: nothing written"
    sqlite3.connect(db).close()  # a file no migration has reached: no table to count
    assert ks.written() == 0
    db.unlink()
    before = ks.missed()
    ks.put_shell(db, [("shell-lint", "a", "{}"), ("shell-lint", "b", "{}")])
    ks.put("parse-findings", {"c": "[]"}, db)
    assert ks.written() == 3 and ks.missed() == before + 3
