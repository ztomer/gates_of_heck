"""The loop queue's one reader in goh (lib/queue_read.py, LOOP.QUEUEARCHIVE): the same queue from
its SQLite store or its JSON file, read-only, and a gate refusing any other parse of it in goh."""

from __future__ import annotations

import ast
import importlib
import json
import os
import sqlite3
from pathlib import Path

import pytest
import queue_hold
import queue_read
import suite_home

ROOT = Path(__file__).resolve().parent.parent
QUEUE = {
    "schema": 2,
    "rules": {"lane_pool": 3},
    "phases": [{"id": "A", "title": "a", "requires": []}],
    "items": [
        {"id": "A.1", "status": "in_progress", "repo": "held", "phase": "A", "agent": "a1"},
        {"id": "A.2", "status": "done", "repo": "free", "phase": "A", "note": "x"},
    ],
}


def write_db(db: Path, q: dict, archived: tuple = ()) -> None:
    """The store as the documented schema in lib/queue_read.py lays it out."""
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA user_version = 1")
    con.execute("CREATE TABLE queue_keys(key TEXT PRIMARY KEY, seq INTEGER, value TEXT)")
    con.execute("CREATE TABLE items(id TEXT PRIMARY KEY, seq INTEGER, archived INTEGER,"
                " status TEXT, repo TEXT, batch TEXT, priority TEXT, phase TEXT, agent TEXT,"
                " sha TEXT, body TEXT)")  # fmt: skip
    meta = [(k, n, json.dumps(v)) for n, (k, v) in enumerate(q.items()) if k != "items"]
    con.executemany("INSERT INTO queue_keys VALUES (?, ?, ?)", meta)
    rows = [(i, 0) for i in q["items"]] + [(i, 1) for i in archived]
    con.executemany(
        "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(i["id"], n, arch, i.get("status"), i.get("repo"), i.get("batch"), i.get("priority"),
          i.get("phase"), i.get("agent"), None, json.dumps(i)) for n, (i, arch) in enumerate(rows)],
    )  # fmt: skip
    con.commit()
    con.close()


def test_json_and_sqlite_stores_read_the_same(tmp_path: Path) -> None:
    named = tmp_path / "queue.json"
    named.write_text(json.dumps(QUEUE))
    assert queue_read.load(named) == QUEUE
    write_db(tmp_path / "loop.db", {**QUEUE, "items": QUEUE["items"][::-1]})
    got = queue_read.load(named)  # the store wins over the file once it exists
    assert [i["id"] for i in got["items"]] == ["A.2", "A.1"] and got["rules"] == QUEUE["rules"]
    named.unlink()  # after the cutover the name is only a name
    assert queue_read.load(named)["phases"] == QUEUE["phases"]
    assert [i["id"] for i in queue_read.items(named)] == ["A.2", "A.1"]


def test_archive_rows_are_not_the_queues_items(tmp_path: Path) -> None:
    old = {"id": "Z.9", "status": "done", "repo": "held", "phase": "A", "agent": "a1"}
    write_db(tmp_path / "loop.db", QUEUE, archived=(old,))
    assert [i["id"] for i in queue_read.items(tmp_path / "queue.json")] == ["A.1", "A.2"]


def test_the_archive_is_its_rows_again(tmp_path: Path) -> None:
    """GOH.QUEUESCHEMA: the loop stores `archive.items` as a null placeholder in queue_keys and the
    archived items as rows with archived = 1; the reader returns the list rebuilt in seq order, as
    the loop's own load does (a None there was a TypeError in the loop's triggers)."""
    old = [{"id": "Z.9", "status": "done"}, {"id": "Z.1", "status": "done"}]
    write_db(tmp_path / "loop.db", {**QUEUE, "archive": {"note": "x", "items": None}},
             archived=tuple(old))  # fmt: skip
    got = queue_read.load(tmp_path / "queue.json")
    assert got["archive"] == {"note": "x", "items": old}
    assert [i["id"] for i in got["items"]] == ["A.1", "A.2"]


REAL_LOOP = Path(os.environ.get(suite_home.MARKER) or suite_home.REAL_HOME) / "Projects" / ".loop"


@pytest.mark.skipif(not (REAL_LOOP / "tools" / "queue_store.py").is_file(),
                    reason="no .loop checkout (its writer makes the store)")  # fmt: skip
def test_a_store_the_loops_writer_made_reads_as_the_loop_reads_it(
    tmp_path: Path, monkeypatch
) -> None:
    """GOH.QUEUESCHEMA: the loop's own writer (queue_store.create) on a temp file, read by goh, is
    what the loop's own load returns -- the archive included."""
    monkeypatch.syspath_prepend(str(REAL_LOOP / "tools"))
    queue_store = importlib.import_module("queue_store")
    q = {**QUEUE, "archive": {"note": "x", "items": [{"id": "Z.9", "status": "done"},
                                                     {"id": "Z.1", "status": "done"}]}}  # fmt: skip
    queue_store.create(tmp_path / "loop.db", q)
    named = tmp_path / "queue.json"
    assert queue_read.load(named) == queue_store.load(named) == q


def test_no_loop_is_an_answer_of_no_queue_never_a_crash(tmp_path: Path, monkeypatch) -> None:
    """goh is public: where no loop runs (no loop.db, no queue.json) every reader answers
    "no queue"."""
    import importlib

    import reuse_fault

    monkeypatch.syspath_prepend(str(ROOT / "hooks" / "claude"))
    spawn_register = importlib.import_module("spawn_register")

    named = tmp_path / "queue.json"
    monkeypatch.setenv(queue_read.ENV, str(named))
    assert queue_hold.find(str(tmp_path / "f.txt"), None, "agent") is None
    assert queue_hold.holds_below(str(tmp_path), None, "agent") == []
    assert "names no phase" in reuse_fault.current_phase({"class_of"})
    assert spawn_register.pending_items("Item A.1", str(named)) == []
    assert queue_read.items_or_empty(named) == []


def test_a_schema_goh_does_not_know_is_no_queue_with_a_reason(tmp_path: Path) -> None:
    db = tmp_path / "loop.db"
    write_db(db, QUEUE)
    con = sqlite3.connect(db)
    con.execute("PRAGMA user_version = 99")  # a newer loop's file
    con.close()
    with pytest.raises(queue_read.NoQueue, match="schema 99, which this goh does not know"):
        queue_read.load(tmp_path / "queue.json")
    assert queue_read.items_or_empty(tmp_path / "queue.json") == []  # an answer, never a crash


def test_reads_never_write_the_store(tmp_path: Path) -> None:
    db = tmp_path / "loop.db"
    write_db(db, QUEUE)
    before = db.read_bytes()
    queue_read.load(tmp_path / "queue.json")
    assert db.read_bytes() == before


def test_absent_is_oserror_unreadable_is_valueerror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        queue_read.load(tmp_path / "queue.json")
    (tmp_path / "queue.json").write_text("[]")
    with pytest.raises(ValueError, match="not a JSON object"):
        queue_read.load(tmp_path / "queue.json")
    (tmp_path / "loop.db").write_text("not a database")
    with pytest.raises(ValueError, match="unreadable"):
        queue_read.load(tmp_path / "queue.json")
    assert queue_read.items_or_empty(tmp_path / "queue.json") == []  # a reader that fails open


def test_the_hold_guard_reads_the_sqlite_store(tmp_path: Path, monkeypatch) -> None:
    """queue_hold judges holds from the store: an in-progress item there holds its repo."""
    projects = tmp_path / "Projects"
    (projects / "held" / ".git").mkdir(parents=True)
    loop = projects / "loop"
    loop.mkdir()
    write_db(loop / "loop.db", QUEUE)
    monkeypatch.setenv(queue_read.ENV, str(loop / "queue.json"))
    hold = queue_hold.find(str(projects / "held" / "f.txt"), None, "intruder")
    assert hold is not None and hold.item == "A.1", hold


# -- the gate: no other parse of the loop queue in goh -------------------------------------------

SEAM = {"lib/queue_read.py"}
SKIP = {"tests", "build", "target", ".git", "node_modules", "__pycache__"}
NOT_THE_LOOP_QUEUE = ("landq", "enqueue")  # goh's own land queue


def _names_queue(src: str) -> bool:
    low = src.lower()
    return "queue" in low and not any(n in low for n in NOT_THE_LOOP_QUEUE)


def direct_parses(root: Path = ROOT) -> list[str]:
    """Every json.load(s), open() or .read_text() of something named a queue, outside the seam."""
    out = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if SKIP & set(rel.parts) or rel.as_posix() in SEAM:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            arg = ast.unparse(n.args[0]) if n.args else ""
            is_json = isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                and f.value.id == "json" and f.attr in ("load", "loads")  # fmt: skip
            is_open = isinstance(f, ast.Name) and f.id == "open"
            is_read = isinstance(f, ast.Attribute) and f.attr in ("read_text", "read_bytes")
            src = ast.unparse(f.value) if is_read else arg
            if (is_json or is_open or is_read) and _names_queue(src):
                out.append(f"{rel.as_posix()}:{n.lineno}: {ast.unparse(n)}")
    return out


def test_no_loop_queue_parse_outside_queue_read() -> None:
    assert direct_parses() == []


def test_a_planted_reader_is_refused(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "planted.py").write_text(
        "import json\nimport os\n\nqueue = os.environ['GOH_LOOP_QUEUE']\n"
        "with open(queue) as fh:\n    q = json.load(fh)\nr = json.loads(Path(queue).read_text())\n"
        "landq = json.loads(Path('landq.json').read_text())\n"
    )
    (tmp_path / "lib" / "queue_read.py").write_text("q = open(queue).read()\n")
    got = direct_parses(tmp_path)
    assert sorted(int(v.split(":")[1]) for v in got) == [5, 7, 7], got
