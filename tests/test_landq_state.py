"""The landing queue's state files are written by one rename and read without crashing on a file
that is gone or not whole (lib/landq_state.py, GOH.CIRED01310).

v0.139.10's hosted CI, under coverage on a 3-core runner: tests/test_landq_look.py's mover wrote
the done record with `write_text` (truncate, then write) and the live `goh land --wait` read it in
the gap, "Expecting value: line 1 column 1 (char 0)". The lander's own writer renames; the test's
copy of it did not, and `wait` and `status` trusted every read. Each torn file here is planted on
purpose, and the wait's re-read is an event the test watches, not a clock.
"""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))
import landq_look
import landq_state

DONE = {"state": "landed", "sha": "b" * 40, "landed": "c" * 40, "mode": "land"}


@pytest.mark.load_sensitive
def test_wait_rereads_a_torn_done_record_until_it_is_whole(tmp_path, monkeypatch):
    q = tmp_path / "landq"
    (q / "done").mkdir(parents=True)
    done = q / "done" / "01-T.S.json"
    done.write_text("")  # the gap a truncating writer leaves: there, and empty
    said: list[str] = []
    monkeypatch.setattr(landq_look, "ok", said.append)
    monkeypatch.setattr(landq_look, "poll", 0.01)
    seen_torn = threading.Event()
    real_read = landq_state.read

    def watched(path: Path) -> dict | None:
        rec = real_read(path)
        if rec is None and path == done:
            seen_torn.set()
        return rec

    monkeypatch.setattr(landq_state, "read", watched)

    def finish() -> None:
        assert seen_torn.wait(patience(30)), "the wait never read the torn record"
        landq_state.write(done, DONE)

    t = threading.Thread(target=finish)
    t.start()
    assert landq_look.wait(q, "T.S", patience(60), lambda: None) == 0
    t.join()
    assert seen_torn.is_set() and "cccccccc" in said[-1], said


@pytest.mark.load_sensitive
def test_wait_names_a_done_record_that_never_becomes_whole(tmp_path, monkeypatch):
    q = tmp_path / "landq"
    (q / "done").mkdir(parents=True)
    (q / "done" / "01-T.S.json").write_text('{"state": "lan')
    said: list[str] = []
    monkeypatch.setattr(landq_look, "err", said.append)
    monkeypatch.setattr(landq_look, "poll", 0.01)
    assert landq_look.wait(q, "T.S", 0.05, lambda: None) == 3
    assert "not a whole record" in said[-1], said


def test_status_skips_a_torn_request_and_reads_the_rest(tmp_path, monkeypatch):
    q = tmp_path / "landq"
    (q / "done").mkdir(parents=True)
    ok = {"item": "T.OK", "repo": "/r/x", "sha": "a" * 40, "mode": "land"}
    landq_state.put(q, "01-T.OK", ok)  # a request is a goh.db row (GOH.LANDQDB): never torn
    (q / "done" / "00-T.HALF.json").write_text('{"state"')
    said: list[str] = []
    monkeypatch.setattr(landq_look, "info", said.append)
    assert landq_look.status(q) == 0
    assert said == ["pending: T.OK x @ aaaaaaaa (land)"], said


def test_a_writer_that_dies_mid_write_leaves_the_old_record_whole(tmp_path, monkeypatch):
    path = tmp_path / "01-T.S.json"
    landq_state.write(path, {"state": "pending"})
    real = Path.write_text

    def dies(self: Path, text: str, *a, **k) -> int:
        real(self, text[: len(text) // 2])
        raise OSError("killed mid-write")

    monkeypatch.setattr(Path, "write_text", dies)
    with pytest.raises(OSError):
        landq_state.write(path, {"state": "landed", "sha": "b" * 40})
    monkeypatch.undo()
    assert landq_state.read(path) == {"state": "pending"}
    assert [p.name for p in tmp_path.glob("*.json")] == ["01-T.S.json"]  # the torn tmp: no glob


# THE CLASS: every write of a landq state record goes through landq_state.write -- the lander's
# and each landq test's stand-in for it, the copy that tore the read -- and every reader of records
# another process writes (the wait, the status, the in-flight markers) through its read.
STATE_WRITERS = sorted([REPO_ROOT / "tools" / "landq.py", *(REPO_ROOT / "lib").glob("landq_*.py"),
                        *(REPO_ROOT / "tests").glob("test_landq*.py")])  # fmt: skip
EXEMPT = {"landq_state.py", "test_landq_state.py"}  # the writer, and the torn writes planted here
CROSS_READERS = [REPO_ROOT / "lib" / "landq_look.py", REPO_ROOT / "lib" / "landq_inflight.py"]
# a JSON written in place, or a hand-made tmp+rename (a second writer); a move between state dirs
# (`pending/x.json` -> `running/x.json`) renames a whole record and is no writer
_JSON_WRITE = re.compile(r"\.write_text\(\s*json\.dumps\(|\btmp\.replace\(")
_JSON_READ = re.compile(r"json\.loads\(")


def _hits(pattern: re.Pattern[str], text: str) -> list[int]:
    return [n for n, line in enumerate(text.splitlines(), 1) if pattern.search(line)]


def test_the_class_scanners_see_a_planted_torn_writer_and_a_bare_reader():
    assert _hits(_JSON_WRITE, "    p.write_text(json.dumps(rec))\n") == [1]
    assert _hits(_JSON_WRITE, "    tmp.replace(path)\n") == [1]
    assert _hits(_JSON_READ, "    r = json.loads(f.read_text())\n") == [1]
    assert _hits(_JSON_WRITE, "    log.write_text(why)\n") == []
    assert _hits(_JSON_WRITE, '    (q / "pending" / path.name).replace(path)\n') == []


def test_every_landq_state_record_is_written_by_one_rename():
    bad = {p.name: n for p in STATE_WRITERS if p.name not in EXEMPT
           if (n := _hits(_JSON_WRITE, p.read_text()))}  # fmt: skip
    assert not bad, f"write these through landq_state.write: {bad}"


def test_every_cross_process_landq_reader_reads_through_landq_state():
    bad = {p.name: n for p in CROSS_READERS if (n := _hits(_JSON_READ, p.read_text()))}
    assert not bad, f"read these through landq_state.read: {bad}"
