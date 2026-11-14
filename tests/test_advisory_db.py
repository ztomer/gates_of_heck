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
    db: Path, tmp_path: Path, mode: str, body: str = "", **extra: str
) -> Iterator[tuple[subprocess.Popen, Path]]:
    record = tmp_path / f"argv-{mode}"
    fake = tmp_path / "bin" / "cargo"
    fake.parent.mkdir(exist_ok=True)
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$GOH_TEST_RECORD"\n' + body)
    fake.chmod(0o755)
    # The index host is one that always resolves, so no test here asks the machine's DNS.
    base = {"GOH_AUDIT_INDEX_HOST": "localhost", **extra}
    env = hermetic_env(GOH_ADVISORY_DB=str(db), GOH_TEST_RECORD=str(record), **base)
    for proxy in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        if proxy not in extra:
            env.pop(proxy, None)
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
    """Every goh `cargo audit` reads the locked copy: the gate, the ignore check, the estate run.
    And every one runs under the audit's own ceiling (`advisory_db.bounded`), never a bare
    subprocess call that a spinning cargo-audit holds for as long as the caller's budget."""
    gate = (REPO_ROOT / "gates" / "rust_gate.sh").read_text()
    assert 'lib/advisory_db.py" run "$fetch" -- cargo audit' in gate
    for rel in ("checks/check_audit_ignores.py", "scripts/estate_audit.py"):
        text = (REPO_ROOT / rel).read_text()
        assert "advisory_db.held(" in text and "advisory_db.audit_args(" in text, rel
        assert "advisory_db.bounded(" in text, rel


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


# GOH.AUDITSPIN (2026-11-14). cargo-audit 0.23.0 spun at 100% CPU for 10.5 and 3.3 minutes in two
# push gates, its output stopped at "Scanning Cargo.lock": the yanked check. tame-index 0.26.3's
# `AsyncRemoteSparseIndex::exec_request` retries a connect error in a loop with no backoff, and the
# first crate's request runs with no deadline at all, so a name lookup of the index that fails FAST
# (the resolver was refusing every lookup around a logoff) is a busy loop in getaddrinfo that never
# ends. Reproduced on demand: `HTTPS_PROXY=http://no-such-host.invalid:3128 cargo audit --deny
# yanked` never finishes, every worker in getaddrinfo -> nw_path_evaluator -> necp_client_action.


def test_a_fetching_audit_whose_index_does_not_resolve_is_refused_before_it_runs(
    db: Path, tmp_path: Path
) -> None:
    """The lookup cargo-audit would spin on is asked first; a refusal names it and the stall."""
    with _started(db, tmp_path, "--fetch", GOH_AUDIT_INDEX_HOST="goh-audit-test.invalid") as (
        proc,
        record,
    ):
        assert proc.wait(timeout=30) != 0
        err = proc.stderr.read().decode()
    assert not record.exists(), "cargo audit ran against an index that does not resolve"
    assert "goh-audit-test.invalid" in err and "does not resolve" in err, err
    assert "GOH_DEPS_OFFLINE=1" in err, err


def test_the_lookup_asked_is_the_proxys_when_one_is_set(db: Path, tmp_path: Path) -> None:
    """cargo-audit's client goes through HTTPS_PROXY, so that is the name it would spin on."""
    with _started(db, tmp_path, "--fetch", HTTPS_PROXY="http://goh-audit-proxy.invalid:3128") as (
        proc,
        record,
    ):
        assert proc.wait(timeout=30) != 0
        err = proc.stderr.read().decode()
    assert not record.exists()
    assert "goh-audit-proxy.invalid" in err, err


def test_an_offline_audit_asks_no_lookup(db: Path, tmp_path: Path) -> None:
    """`--no-fetch` reads the cached index only, so an unresolvable name is no reason to refuse."""
    with _started(db, tmp_path, "--no-fetch", GOH_AUDIT_INDEX_HOST="goh-audit-test.invalid") as (
        proc,
        record,
    ):
        assert proc.wait(timeout=30) == 0
    assert record.exists()


SPIN = (
    'exec python3 -c "import time\n'
    "end = time.monotonic() + 20\n"
    'while time.monotonic() < end: pass"\n'
)


def test_an_audit_that_burns_its_cpu_ceiling_is_stopped_and_named(db: Path, tmp_path: Path) -> None:
    """The spin itself: CPU, not wall, is what tells it from a wait on cargo's package lock."""
    with _started(db, tmp_path, "--fetch", body=SPIN, GOH_AUDIT_CPU="1", GOH_AUDIT_WALL="60") as (
        proc,
        record,
    ):
        rc = proc.wait(timeout=15)
        err = proc.stderr.read().decode()
    assert rc == 124, (rc, err)
    assert "CPU" in err and "GOH.AUDITSPIN" in err and "yanked" in err, err
    assert record.exists()


def test_an_audit_that_outlives_its_wall_ceiling_is_stopped_and_named(
    db: Path, tmp_path: Path
) -> None:
    with _started(db, tmp_path, "--no-fetch", body="exec sleep 30\n", GOH_AUDIT_WALL="1") as (
        proc,
        _record,
    ):
        rc = proc.wait(timeout=15)
        err = proc.stderr.read().decode()
    assert rc == 124, (rc, err)
    assert "1 s" in err and "GOH.AUDITSPIN" in err, err


def test_bounded_captures_and_names_the_stall_last(tmp_path: Path, monkeypatch) -> None:
    """The python callers read stdout as JSON and quote stderr's LAST line on a failure."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("advisory_db_under_test", SEAM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("GOH_AUDIT_WALL", "1")
    r = mod.bounded(["sh", "-c", "echo out; echo err >&2; exec sleep 30"], fetch=False)
    assert r.returncode == 124 and r.stdout == "out\n", r
    assert "GOH.AUDITSPIN" in r.stderr.strip().splitlines()[-1], r.stderr
    ok = mod.bounded(["sh", "-c", 'printf "{}"'], fetch=False)
    assert (ok.returncode, ok.stdout) == (0, "{}"), ok
