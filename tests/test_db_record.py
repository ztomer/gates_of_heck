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

import io
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import db_core
import db_record
from _tree_clone import from_template
from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import patience

COPY = ("lib/db_core.py", "lib/db_record.py", "lib/db_schema.py", "lib/db_settings.py",
        "lib/db_waits.py", "lib/goh_db.py", "lib/lock_order.py", "lib/lock_order.tsv",
        "lib/pid_owner.py", "lib/goh_db_schema", "lib/state_paths.py", "tui/lib.py",
        "tui/stylerc")  # fmt: skip
TREE_READS = ("lib/db_core.py", "lib/db_record.py", "lib/db_schema.py", "lib/db_settings.py",
              "lib/db_waits.py", "lib/goh_db.py", "lib/lock_order.py", "lib/lock_order.tsv",
              "lib/pid_owner.py", "lib/goh_db_schema/", "lib/state_paths.py", "tui/lib.py",
              "tui/stylerc")  # fmt: skip

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


def _build_main(main: Path) -> None:
    """The stand-in main checkout: goh.db's seam, committed (built once per run)."""
    for rel in COPY:
        src, dst = REPO_ROOT / rel, main / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
    # Its first migrations: the record rule reads their numbers, and all of them are 0.7 MB a copy.
    for late in sorted((main / "lib" / "goh_db_schema").glob("*.sql"))[3:]:
        late.unlink()
    git(main, "init", "-q", "-b", "main")
    _commit(main, "goh.db's seam")


def _main_copy(tmp_path_factory, tmp_path: Path) -> Path:
    return from_template(tmp_path_factory, "db-record-main", _build_main, tmp_path / "main")


def _shipped(copy: Path) -> list[int]:
    """The numbers COPY ships: it makes a new file with every one."""
    return sorted(int(p.name[:4]) for p in (copy / "lib" / "goh_db_schema").glob("*.sql"))


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


def test_only_the_code_of_record_migrates_the_shared_file(tmp_path_factory, tmp_path) -> None:
    main = _main_copy(tmp_path_factory, tmp_path)
    shipped = _shipped(main)
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


def test_a_copy_never_creates_the_shared_file(tmp_path_factory, tmp_path) -> None:
    main = _main_copy(tmp_path_factory, tmp_path)
    lane = tmp_path / "lane"
    git(main, "worktree", "add", "-q", "-b", "lane", str(lane))
    got = _write(tmp_path, lane, "l1")
    assert got.startswith("refused:") and "code of record" in got, got
    assert not (tmp_path / "state" / "goh" / "goh.db").exists()


def _export(src: Path, rev: str, dest: Path, live: Path | None) -> Path:
    """A copy shaped as gates/_from_head.sh makes one: `git archive REV` with no `.git`, the rev in
    `.goh-head` and the checkout that made it in `.goh-live` (NONE: canary_sides' export)."""
    sha = git(src, "rev-parse", rev).strip()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tar = subprocess.run(["git", "-C", str(src), "archive", sha], check=True, capture_output=True,
                         stdin=subprocess.DEVNULL).stdout  # fmt: skip
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(dest, filter="data")
    (dest / ".goh-head").write_text(f"{sha}\n")
    if live is not None:
        (dest / ".goh-live").write_text(f"{live}\n")
    return dest


def test_a_head_copy_of_a_commit_not_on_main_never_migrates_the_shared_file(
    tmp_path_factory, tmp_path
) -> None:
    """GOH.HEADCOPYRECORD. 2026-11-24 00:34:55: the SHIPSV2 lane's pre-commit exported its STAGED
    TREE to ~/.cache/goh/head/7b705d90.. (no `.git`), and commit_stamp.py's `record` from that copy
    applied 0009 to the real goh.db at 00:35:25 ("goh unknown"), before 0009 was on main: the rule
    read a directory with no `.git` as an install. A copy is of record by its COMMIT."""
    main = _main_copy(tmp_path_factory, tmp_path)
    shipped = _shipped(main)
    assert _write(tmp_path, main, "m1") == "wrote"
    lane = tmp_path / "lane"
    git(main, "worktree", "add", "-q", "-b", "lane", str(lane))
    (lane / "lib" / "goh_db_schema" / "0099_lane.sql").write_text("CREATE TABLE lane(x);\n")
    git(lane, "add", "-A")
    tree = git(lane, "write-tree").strip()  # what a goh commit's pre-commit exports (SELFGATE)
    staged = _export(lane, tree, tmp_path / "head" / tree, lane)
    got = _write(tmp_path, staged, "t1")
    assert got.startswith("refused:") and "0099_lane" in got, got
    _commit(lane, "the lane's migration")
    commit = _export(lane, "HEAD", tmp_path / "head" / "lane-tip", lane)
    got = _write(tmp_path, commit, "c1")
    assert got.startswith("refused:") and "0099_lane" in got, got
    side = _export(lane, "HEAD", tmp_path / "canary" / "head", None)  # canary_sides: no .goh-live
    got = _write(tmp_path, side, "s1")
    assert got.startswith("refused:") and "0099_lane" in got, got
    assert _file(tmp_path) == (shipped, "m1"), "a head copy's migration reached the shared file"


def test_a_head_copy_of_mains_tip_migrates_the_shared_file(tmp_path_factory, tmp_path) -> None:
    """The other side: main's own commit, run from its export (a consumer's gate runs goh's HEAD
    from ~/.cache/goh/head/<sha>), is the code of record, and an ancestor of main's tip is too."""
    main = _main_copy(tmp_path_factory, tmp_path)
    shipped = _shipped(main)
    assert _write(tmp_path, main, "m1") == "wrote"
    old = _export(main, "HEAD", tmp_path / "head" / "old", main)
    (main / "lib" / "goh_db_schema" / "0098_main.sql").write_text("CREATE TABLE landed(x);\n")
    _commit(main, "main's own migration")
    assert _write(tmp_path, old, "o1") == "wrote"  # an ancestor of main: of record, no migration
    tip = _export(main, "HEAD", tmp_path / "head" / "tip", main)
    assert _write(tmp_path, tip, "h1") == "wrote"
    assert _file(tmp_path) == ([*shipped, 98], "h1")


def test_a_copy_that_names_no_commit_is_not_of_record(tmp_path_factory, tmp_path) -> None:
    """No `.git` and no `.goh-head` (a bare `git archive` such as lib/selection_miss.py's, a push
    gate's kept tree): nothing shows its commit is main's, so it migrates no shared file."""
    main = _main_copy(tmp_path_factory, tmp_path)
    assert _write(tmp_path, main, "m1") == "wrote"
    bare = _export(main, "HEAD", tmp_path / "bare", None)
    (bare / ".goh-head").unlink()
    (bare / "lib" / "goh_db_schema" / "0097_bare.sql").write_text("CREATE TABLE bare(x);\n")
    got = _write(tmp_path, bare, "b1")
    assert got.startswith("refused:") and "0097_bare" in got, got


def test_the_record_is_a_commit_on_main(tmp_path) -> None:
    """A linked worktree's `.git` is a file: never. A primary checkout on main: yes. A directory
    with no git that names no commit: no (it was "an install", which let a head copy migrate)."""

    def why() -> str | None:
        db_record._RECORD.clear()  # the answer is kept per process; this test changes the tree
        return db_core.why_not_of_record(tmp_path)

    assert "names no commit" in str(why())
    (tmp_path / ".git").write_text("gitdir: /x/.git/worktrees/w\n")
    assert "linked worktree" in str(why())
    (tmp_path / ".git").unlink()
    (tmp_path / ".git").mkdir()  # a primary checkout as the rule reads it: its HEAD file
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    assert why() is None
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/lane\n")
    assert "not main" in str(why())  # a primary checkout on another branch with no commit yet
