"""Only the code of record migrates the real state dbs (GOH.LANEMIGRATE).

THE CASE (2026-11-23). `goh land --check` run from lane worktrees opened the real goh.db with the
lane's code and applied migrations main did not have yet (S1's 0002, then S3's 0004). Main's goh
then saw a migration it does not ship, and every goh.db write raised NewerFile until those lanes
landed: land rows and durations stopped.

Here a stand-in "main" checkout (a git repository holding goh.db's seam) and a lane worktree of it
each write the shared file through a child process whose state home is a temp directory (never the
real goh.db). The lane's own migration never reaches the shared file, main's writes keep working,
and main's own new migration still applies, from main only.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import db_core
from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import patience

COPY = ("lib/db_core.py", "lib/goh_db.py", "lib/lock_order.py", "lib/lock_order.tsv",
        "lib/pid_owner.py", "lib/goh_db_schema", "tui/lib.py", "tui/stylerc")  # fmt: skip

DRIVER = """
import sys
sys.path.insert(0, sys.argv[1])
import db_core, goh_db
try:
    with goh_db.transaction() as con:
        con.execute("INSERT INTO meta VALUES ('probe', ?) ON CONFLICT(key) DO UPDATE SET "
                    "value=excluded.value", (sys.argv[2],))
    print("wrote")
except db_core.NewerFile as e:
    print("refused:", e)
"""


def _main_copy(tmp_path: Path) -> Path:
    main = tmp_path / "main"
    for rel in COPY:
        src, dst = REPO_ROOT / rel, main / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
    git(main, "init", "-q", "-b", "main")
    _commit(main, "goh.db's seam")
    return main


def _commit(where: Path, msg: str) -> None:
    git(where, "add", "-A")
    git(where, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", msg)  # fmt: skip


def _write(tmp_path: Path, copy: Path, probe: str) -> str:
    """A write to the shared goh.db from COPY's code, in a child whose state home is tmp's (no
    suite marker: the child is a goh run, and its XDG state home is the one its main shares)."""
    env = hermetic_env(XDG_STATE_HOME=str(tmp_path / "state"), HOME=str(tmp_path / "home"))
    for k in (db_core.REAL_HOME_ENV, db_core.REAL_XDG_STATE_ENV):
        env.pop(k, None)
    (tmp_path / "driver.py").write_text(DRIVER, encoding="utf-8")
    got = subprocess.run([sys.executable, str(tmp_path / "driver.py"), str(copy / "lib"), probe],
                         capture_output=True, text=True, check=False, env=env, cwd=tmp_path,
                         stdin=subprocess.DEVNULL, timeout=patience(60))  # fmt: skip
    assert got.returncode == 0, got.stderr
    return got.stdout.strip()


def _file(tmp_path: Path) -> tuple[list[int], str | None]:
    """The shared file's applied migrations and its last probe."""
    con = sqlite3.connect(f"{(tmp_path / 'state' / 'goh' / 'goh.db').as_uri()}?mode=ro", uri=True)
    try:
        applied = sorted(r[0] for r in con.execute("SELECT version FROM schema_version"))
        probe = con.execute("SELECT value FROM meta WHERE key='probe'").fetchone()
        return applied, probe and probe[0]
    finally:
        con.close()


def test_only_the_code_of_record_migrates_the_shared_file(tmp_path) -> None:
    main = _main_copy(tmp_path)
    shipped = sorted(int(p.name[:4]) for p in (main / "lib" / "goh_db_schema").glob("*.sql"))
    assert _write(tmp_path, main, "m1") == "wrote"  # main creates and migrates it
    assert _file(tmp_path) == (shipped, "m1")

    lane = tmp_path / "lane"
    git(main, "worktree", "add", "-q", "-b", "lane", str(lane))
    assert _write(tmp_path, lane, "l1") == "wrote"  # same migrations as the file: it writes
    assert _file(tmp_path) == (shipped, "l1")

    (lane / "lib" / "goh_db_schema" / "0099_lane.sql").write_text("CREATE TABLE lane(x);\n")
    got = _write(tmp_path, lane, "l2")
    assert got.startswith("refused:") and "0099_lane" in got, got
    assert _file(tmp_path) == (shipped, "l1"), "a lane's migration reached the shared file"
    assert _write(tmp_path, main, "m2") == "wrote"  # main's writes keep working
    assert _file(tmp_path) == (shipped, "m2")

    (main / "lib" / "goh_db_schema" / "0098_main.sql").write_text("CREATE TABLE landed(x);\n")
    _commit(main, "main's own migration")
    git(lane, "-c", "user.name=t", "-c", "user.email=t@t", "merge", "-q", "main")
    got = _write(tmp_path, lane, "l3")  # a land's pre-step carries 0098 too: it is not main
    assert got.startswith("refused:") and "0098_main" in got, got
    assert _file(tmp_path) == (shipped, "m2")
    assert _write(tmp_path, main, "m3") == "wrote"  # the next main run applies it
    assert _file(tmp_path) == ([*shipped, 98], "m3")


def test_a_copy_never_creates_the_shared_file(tmp_path) -> None:
    main = _main_copy(tmp_path)
    lane = tmp_path / "lane"
    git(main, "worktree", "add", "-q", "-b", "lane", str(lane))
    got = _write(tmp_path, lane, "l1")
    assert got.startswith("refused:") and "code of record" in got, got
    assert not (tmp_path / "state" / "goh" / "goh.db").exists()


def test_the_record_is_a_primary_checkout(tmp_path) -> None:
    """A linked worktree's `.git` is a file; a primary checkout's a directory; an install with no
    git at all is its own record."""
    assert db_core.of_record(tmp_path)
    (tmp_path / ".git").mkdir()
    assert db_core.of_record(tmp_path)
    os.rmdir(tmp_path / ".git")
    (tmp_path / ".git").write_text("gitdir: /x/.git/worktrees/w\n")
    assert not db_core.of_record(tmp_path)
