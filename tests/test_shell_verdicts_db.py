"""The goh binary's per-file verdicts are rows of goh-cache.db (GOH.VERDICTDB V2).

`~/.cache/goh/verdicts` held them as one JSON file each: 175,644 files, 1.3 GB, no bound. They are
rows of goh-cache.db's `shell_verdicts` (0003, 30 days and 200k rows), read and written by the
binary in-process (rusqlite, bundled SQLite: decision (f)1). The binary never migrates the file:
lib/goh_cache_db.py does, so Python and Rust open one file. A cache that cannot be opened is a
miss for every file, never a different result.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import _kept_store as ks
import pytest
from conftest import git, write

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import db_bounds
import goh_cache_db

needs_shellcheck = pytest.mark.skipif(shutil.which("shellcheck") is None, reason="needs shellcheck")
CLEAN = "#!/usr/bin/env bash\necho hi\n"
RED = "#!/usr/bin/env bash\nprintf '%s' $@\n"  # SC2068


def test_0003_is_shell_verdicts_bounded_at_a_month_and_200k_rows(tmp_path) -> None:
    with goh_cache_db.connect(tmp_path / "c.db") as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(shell_verdicts)")]
        assert 3 in goh_cache_db.db_core.version(con)["applied"]
    assert cols == ["checker", "key", "record", "at"]
    b = db_bounds.bounds(goh_cache_db.SEAM)["shell_verdicts"]
    assert (b.days, b.rows, b.stamp_col) == (30, 200_000, "at")


@pytest.fixture
def lint(repo: Path, tmp_path: Path, goh: Path):
    real = shutil.which("shellcheck")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "shellcheck.log"
    fake = bindir / "shellcheck"
    fake.write_text(f'#!/bin/sh\nfor a in "$@"; do echo "$a" >> {log}; done\nexec {real} "$@"\n')
    fake.chmod(0o755)
    write(repo, "a.sh", CLEAN)
    write(repo, "b.sh", RED)
    git(repo, "add", "-A")
    db = tmp_path / "cache" / "goh-cache.db"

    def run(**extra: str):
        log.write_text("")
        env = {k: v for k, v in os.environ.items() if k != "SHELLCHECK_OPTS"}
        env.update(PATH=f"{bindir}:{os.environ['PATH']}", GOH_CACHE_DB=str(db))
        env.update(extra)
        r = subprocess.run([str(goh), "shell-lint"], cwd=repo, capture_output=True, text=True,
                           env=env, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
        asked = {Path(a).name for a in log.read_text().split() if a.endswith(".sh")}
        return (r.returncode, r.stdout, r.stderr), asked

    run.db = db
    return run


def _rows(db: Path) -> list[tuple[str, str]]:
    with goh_cache_db.ro(db) as con:
        return con.execute("SELECT checker, record FROM shell_verdicts ORDER BY checker, record"
                           ).fetchall()  # fmt: skip


@needs_shellcheck
def test_the_binary_writes_rows_python_reads_and_no_record_file(lint, tmp_path) -> None:
    """Python migrates (the binary never does), Rust writes, Python reads the same file."""
    cold, asked = lint()
    assert cold[0] == 1 and asked == {"a.sh", "b.sh"}, cold
    rows = _rows(lint.db)
    assert [c for c, _ in rows].count("shell-lint") == 2, rows
    assert "shellcheck-version" in {c for c, _ in rows}, rows
    assert not list(tmp_path.rglob("*.json")), "a verdict was written as a file"
    warm, asked = lint()
    assert warm == cold and asked == set(), (warm, asked)


@needs_shellcheck
@pytest.mark.parametrize("plant", ["not a database", "directory"])
def test_a_cache_that_cannot_open_is_every_key_a_miss_and_the_same_result(lint, plant) -> None:
    """The failure path: every file is judged by the tools, the verdict is unchanged, and the run
    says nothing it would not say uncached."""
    off, _ = lint(GOH_VERDICT_CACHE="0")
    lint.db.parent.mkdir(parents=True, exist_ok=True)
    if plant == "directory":
        lint.db.mkdir()
    else:
        lint.db.write_bytes(b"\x00garbage, not SQLite" * 64)
    for _ in range(2):
        broken, asked = lint()
        assert asked == {"a.sh", "b.sh"}, asked
        assert broken[:2] == off[:2], (broken, off)


@needs_shellcheck
def test_a_newer_goh_s_file_is_read_and_never_written(lint) -> None:
    lint()
    with sqlite3.connect(lint.db) as con:
        con.execute("DELETE FROM shell_verdicts")
        con.execute("INSERT INTO schema_version VALUES (9999, '9999_future', 0, 'goh 99')")
    con.close()
    lint()
    assert _rows(lint.db) == []


def test_the_kept_merge_carries_shell_verdicts(tmp_path) -> None:
    suite, real = tmp_path / "suite.db", tmp_path / "real.db"
    ks.put_shell(suite, [("shell-lint", "k1", "[]"), ("shell-lint", "k2", "[1]")])
    ks.put_shell(real, [("shell-lint", "k2", "[2]")])
    assert ks.merge(suite, real, False) == 1  # k1 alone: k2's row is already the real file's
    with goh_cache_db.ro(real) as con:
        got = con.execute("SELECT key, record FROM shell_verdicts ORDER BY key").fetchall()
    assert got == [("k1", "[]"), ("k2", "[2]")]
