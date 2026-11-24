"""The release canary's record is written only by a copy that may write goh.db (GOH.STATEDBS4).

A land's push worktree is not the code of record (GOH.LANEMIGRATE): when the lane ships a
migration the real goh.db does not hold yet, goh.db opens read-only there and the canary's record
write raised NotOfRecord, so every land carrying a new migration went red on the canary step with
no consumer red at all. The verdict is the run's exit code; the record is the memo of it, and a
copy that may not write goh.db leaves the record to the code of record's next run, said once."""

from __future__ import annotations

import importlib.util
import sys

from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "release_canary.py"
sys.path.insert(0, str(REPO_ROOT / "lib"))
db_core = importlib.import_module("db_core")  # lib/, on the path from the line above


def _mod():
    spec = importlib.util.spec_from_file_location("release_canary_not_of_record", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _refuse(*_a, **_k):
    raise db_core.NotOfRecord(
        "goh.db lacks migration 0005_x, and this copy is not the code of record"
    )


def test_a_copy_that_may_not_write_goh_db_keeps_the_canarys_verdict(tmp_path, monkeypatch, capsys):
    mod = _mod()
    said = []
    monkeypatch.setattr(sys, "argv", ["release_canary.py", "--state", str(tmp_path / "s.json")])
    monkeypatch.setattr(mod.estate_registry, "update_state", _refuse)
    monkeypatch.setattr(mod, "say", said.append)
    for rc in (0, 1):
        monkeypatch.setattr(mod, "run", lambda _a, rc=rc: ({"memo_records": {}}, rc))
        assert mod.main() == rc
    assert len(said) == 2  # the verdict is still said
    assert "record was not written" in capsys.readouterr().err
