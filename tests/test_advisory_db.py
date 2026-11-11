"""No goh audit reads the advisory database while another rewrites it (`lib/advisory_db.py`).

On 2026-10-12 a land's push gate refreshed the shared `~/.cargo/advisory-db` with one `cargo
audit` while its test suite's fixture gates read it with `--no-fetch`; two of three crates failed
`parse error: unexpected start of advisory: ""` on a file caught half-written. goh now audits its
own copy under a flock: shared to read, exclusive to refresh. A fake `cargo` records the argv it
was given, so these tests read the lock and the arguments, never the network.
"""

from __future__ import annotations

import fcntl
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env

SEAM = REPO_ROOT / "lib" / "advisory_db.py"
BLOCKED_FOR_S = 1.5


@pytest.fixture
def db(tmp_path: Path) -> Path:
    d = tmp_path / "advisory-db"
    d.mkdir()
    return d


@contextmanager
def _started(
    db: Path, tmp_path: Path, mode: str, **extra: str
) -> Iterator[tuple[subprocess.Popen, Path]]:
    record = tmp_path / f"argv-{mode}"
    fake = tmp_path / "bin" / "cargo"
    fake.parent.mkdir(exist_ok=True)
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$GOH_TEST_RECORD"\n')
    fake.chmod(0o755)
    env = hermetic_env(GOH_ADVISORY_DB=str(db), GOH_TEST_RECORD=str(record), **extra)
    env["PATH"] = f"{fake.parent}:{env['PATH']}"
    with subprocess.Popen(
        ["python3", str(SEAM), "run", mode, "--", "cargo", "audit", "--file", "Cargo.lock"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        try:
            yield proc, record
        finally:
            if proc.poll() is None:
                proc.kill()


def _held(db: Path, how: int):
    f = open(f"{db}.goh-lock", "a", encoding="utf-8")  # noqa: SIM115  (held across the test)
    fcntl.flock(f, how)
    return f


def _still_waiting(proc: subprocess.Popen) -> bool:
    try:
        proc.wait(timeout=BLOCKED_FOR_S)
    except subprocess.TimeoutExpired:
        return True
    return False


def test_a_read_waits_while_a_refresh_holds_the_database(db: Path, tmp_path: Path) -> None:
    refresh = _held(db, fcntl.LOCK_EX)
    with _started(db, tmp_path, "--no-fetch") as (proc, record):
        try:
            assert _still_waiting(proc), "a read ran while the database was being refreshed"
            assert not record.exists()
        finally:
            refresh.close()
        assert proc.wait(timeout=30) == 0
    assert record.read_text().split() == [
        "audit",
        "--file",
        "Cargo.lock",
        "--db",
        str(db),
        "--no-fetch",
    ]


def test_a_refresh_waits_for_a_read_in_progress(db: Path, tmp_path: Path) -> None:
    read = _held(db, fcntl.LOCK_SH)
    with _started(db, tmp_path, "--fetch") as (proc, record):
        try:
            assert _still_waiting(proc), "a refresh rewrote the database under a read"
        finally:
            read.close()
        assert proc.wait(timeout=30) == 0
    assert "--no-fetch" not in record.read_text().split() and str(db) in record.read_text()


def test_reads_run_together(db: Path, tmp_path: Path) -> None:
    other_read = _held(db, fcntl.LOCK_SH)
    try:
        with _started(db, tmp_path, "--no-fetch") as (proc, record):
            assert proc.wait(timeout=30) == 0, "a read waited for another read"
        assert record.exists()
    finally:
        other_read.close()


def test_the_house_audits_go_through_the_seam() -> None:
    """Every goh `cargo audit` reads the locked copy: the gate, the ignore check, the estate run."""
    gate = (REPO_ROOT / "gates" / "rust_gate.sh").read_text()
    assert 'lib/advisory_db.py" run "$fetch" -- cargo audit' in gate
    for rel in ("checks/check_audit_ignores.py", "scripts/estate_audit.py"):
        text = (REPO_ROOT / rel).read_text()
        assert "advisory_db.held(" in text and "advisory_db.audit_args(" in text, rel


def test_an_offline_read_with_no_copy_anywhere_fetches_it_once(tmp_path: Path) -> None:
    """Run 42950563400: a hosted runner's fresh HOME had no `~/.cargo/advisory-db` to seed from, and
    `--no-fetch` (GOH_DEPS_OFFLINE=1) audited against nothing, so test_crates_behind's gate died.
    The module says a missing copy is "fetched by the audit itself": offline means no REFRESH, and
    with no copy at all there is nothing to judge against until the first fetch."""
    missing = tmp_path / "none" / "advisory-db"
    bare = tmp_path / "home"  # no ~/.cargo/advisory-db to seed from, as on the hosted runner
    bare.mkdir()
    with _started(missing, tmp_path, "--no-fetch", HOME=str(bare)) as (proc, record):
        assert proc.wait(timeout=30) == 0
    got = record.read_text().split()
    assert "--no-fetch" not in got and str(missing) in got, got
