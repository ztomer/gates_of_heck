"""`goh state test-ships` (lib/ship_records.py test_rows, GOH.FAKESHIPROWS): the ship rows a test
left in a live goh.db -- a result path under the system's temp roots -- are listed, and only with
--delete removed; a row anywhere else, or in the ship directory itself, is never one.

Before .loop's test_ship_done was made hermetic it wrote 30+ ship rows (item A.0, results under
pytest's tmp paths) into the live goh.db, where every ship statistic counted them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import goh_db
import ship_records
import state_retire as sr

sys.path.pop(0)

TMP = "/private/var/folders/0f/xyz/T/pytest-of-u/pytest-3201/popen-gw0/test_a_green_0"
TMP_ROOTS = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GOH_DB", str(tmp_path / "goh.db"))
    monkeypatch.setenv("GOH_SHIP_DIR", str(tmp_path / "ship"))
    return tmp_path / "goh.db"


def _row(result: str, item: str = "A.0") -> str:
    ship_records.put({"result": result, "repo": "/r/x", "name": "x", "item": item, "sha": "a" * 40,
                      "status": "ok", "started": "2026-11-24T05:16:57+00:00"})  # fmt: skip
    return result


def _results() -> list[str]:
    with goh_db.ro() as con:
        return sorted(r for (r,) in con.execute("SELECT result FROM ships"))


def test_a_row_under_a_temp_root_is_listed_and_only_deleted_with_delete(db, capsys):
    fake = _row(f"{TMP}/ship.json")
    other = _row("/tmp/pytest-of-u/pytest-1/t0/ship.json")
    ship_records.put_pins(fake, {"schema": 2, "tag": "v1", "repos": {}})
    assert ship_records.test_rows(TMP_ROOTS) == sorted([fake, other])
    assert sr.main(["test-ships"]) == 0
    out = capsys.readouterr().out
    assert fake in out and "2 test ship rows" in out and "none deleted" in out, out
    assert _results() == sorted([fake, other])  # the listing deleted nothing
    assert sr.main(["test-ships", "--delete"]) == 0
    assert _results() == []
    assert ship_records.get_pins(fake) is None  # its pin row went with it
    assert "2 test ship rows deleted" in capsys.readouterr().out


def test_a_real_path_row_is_refused_and_kept(db, tmp_path):
    """A ship directory's row (here, a test's ship directory under its own tmp) and a path under no
    temp root are real records: never listed, never deleted, whatever the roots say."""
    real = _row(str(Path.home() / ".local/state/goh/ship/gates_of_heck/20261123T190442Z-9.json"))
    ours = _row(str(tmp_path / "ship" / "x" / "20261124T000000Z-1.json"), item="X.ONE")
    near = _row("/tmpfoo/ship.json")  # a sibling named like a root is under no root
    fake = _row(f"{TMP}/ship.json")
    roots = (*TMP_ROOTS, str(tmp_path))  # even a root that holds the ship directory
    assert ship_records.test_rows(roots) == [fake]
    assert ship_records.drop_test_rows(roots) == 1
    assert _results() == sorted([real, ours, near])


def test_the_default_roots_hold_the_temp_dir_this_process_names(db):
    import tempfile

    roots = ship_records.temp_roots()
    assert str(Path(tempfile.gettempdir()).resolve()) in roots
    assert {"/tmp", "/private/tmp", "/var/folders", "/private/var/folders"} <= set(roots)
