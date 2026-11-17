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
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

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


@pytest.mark.load_sensitive
def test_a_read_waits_while_a_refresh_holds_the_database(db: Path, tmp_path: Path) -> None:
    refresh = _held(db, fcntl.LOCK_EX)
    with _started(db, tmp_path, "--no-fetch") as (proc, record):
        try:
            assert _still_waiting(proc), "a read ran while the database was being refreshed"
            assert not record.exists()
        finally:
            refresh.close()
        assert proc.wait(timeout=patience(30)) == 0
    assert record.read_text().split() == [
        "audit",
        "--file",
        "Cargo.lock",
        "--db",
        str(db),
        "--no-fetch",
    ]


@pytest.mark.load_sensitive
def test_a_refresh_waits_for_a_read_in_progress(db: Path, tmp_path: Path) -> None:
    read = _held(db, fcntl.LOCK_SH)
    with _started(db, tmp_path, "--fetch") as (proc, record):
        try:
            assert _still_waiting(proc), "a refresh rewrote the database under a read"
        finally:
            read.close()
        assert proc.wait(timeout=patience(30)) == 0
    assert "--no-fetch" not in record.read_text().split() and str(db) in record.read_text()


@pytest.mark.load_sensitive
def test_reads_run_together(db: Path, tmp_path: Path) -> None:
    other_read = _held(db, fcntl.LOCK_SH)
    try:
        with _started(db, tmp_path, "--no-fetch") as (proc, record):
            assert proc.wait(timeout=patience(30)) == 0, "a read waited for another read"
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


@pytest.mark.load_sensitive
def test_an_offline_read_with_no_copy_anywhere_fetches_it_once(tmp_path: Path) -> None:
    """Run 42950563400: a hosted runner's fresh HOME had no `~/.cargo/advisory-db` to seed from, and
    `--no-fetch` (GOH_DEPS_OFFLINE=1) audited against nothing, so test_crates_behind's gate died.
    The module says a missing copy is "fetched by the audit itself": offline means no REFRESH, and
    with no copy at all there is nothing to judge against until the first fetch."""
    missing = tmp_path / "none" / "advisory-db"
    bare = tmp_path / "home"  # no ~/.cargo/advisory-db to seed from, as on the hosted runner
    bare.mkdir()
    with _started(missing, tmp_path, "--no-fetch", HOME=str(bare)) as (proc, record):
        assert proc.wait(timeout=patience(30)) == 0
    got = record.read_text().split()
    assert "--no-fetch" not in got and str(missing) in got, got


# GOH.AUDITSPIN (2026-11-14). cargo-audit 0.23.0 spun at 100% CPU for 10.5 and 3.3 minutes in two
# push gates, its output stopped at "Scanning Cargo.lock": the yanked check. tame-index 0.26.3's
# `AsyncRemoteSparseIndex::exec_request` retries a connect error in a loop with no backoff, and the
# first crate's request runs with no deadline at all, so a name lookup of the index that fails FAST
# (the resolver was refusing every lookup around a logoff) is a busy loop in getaddrinfo that never
# ends. Reproduced on demand: `HTTPS_PROXY=http://no-such-host.invalid:3128 cargo audit --deny
# yanked` never finishes, every worker in getaddrinfo -> nw_path_evaluator -> necp_client_action.


@pytest.mark.load_sensitive
def test_a_fetching_audit_whose_index_does_not_resolve_is_refused_before_it_runs(
    db: Path, tmp_path: Path
) -> None:
    """The lookup cargo-audit would spin on is asked first; a refusal names it and the stall."""
    with _started(db, tmp_path, "--fetch", GOH_AUDIT_INDEX_HOST="goh-audit-test.invalid") as (
        proc,
        record,
    ):
        assert proc.wait(timeout=patience(30)) != 0
        err = proc.stderr.read().decode()
    assert not record.exists(), "cargo audit ran against an index that does not resolve"
    assert "goh-audit-test.invalid" in err and "does not resolve" in err, err
    assert "GOH_DEPS_OFFLINE=1" in err, err


@pytest.mark.load_sensitive
def test_the_lookup_asked_is_the_proxys_when_one_is_set(db: Path, tmp_path: Path) -> None:
    """cargo-audit's client goes through HTTPS_PROXY, so that is the name it would spin on."""
    with _started(db, tmp_path, "--fetch", HTTPS_PROXY="http://goh-audit-proxy.invalid:3128") as (
        proc,
        record,
    ):
        assert proc.wait(timeout=patience(30)) != 0
        err = proc.stderr.read().decode()
    assert not record.exists()
    assert "goh-audit-proxy.invalid" in err, err


@pytest.mark.load_sensitive
def test_an_offline_audit_asks_no_lookup(db: Path, tmp_path: Path) -> None:
    """`--no-fetch` reads the cached index only, so an unresolvable name is no reason to refuse."""
    with _started(db, tmp_path, "--no-fetch", GOH_AUDIT_INDEX_HOST="goh-audit-test.invalid") as (
        proc,
        record,
    ):
        assert proc.wait(timeout=patience(30)) == 0
    assert record.exists()


# A busy loop that ends by itself after 2e9 steps (~20 s): a CPU ceiling must stop it long before.
SPIN = "exec python3 -c 'n = 0\nwhile n < 2_000_000_000: n += 1'\n"


@pytest.mark.load_sensitive
def test_an_audit_that_burns_its_cpu_ceiling_is_stopped_and_named(db: Path, tmp_path: Path) -> None:
    """The spin itself: CPU, not wall, is what tells it from a wait on cargo's package lock."""
    with _started(db, tmp_path, "--fetch", body=SPIN, GOH_AUDIT_CPU="1", GOH_AUDIT_WALL="60") as (
        proc,
        record,
    ):
        rc = proc.wait(timeout=patience(15))
        err = proc.stderr.read().decode()
    assert rc == 124, (rc, err)
    assert "CPU" in err and "GOH.AUDITSPIN" in err and "yanked" in err, err
    assert record.exists()


@pytest.mark.load_sensitive
def test_an_audit_that_outlives_its_wall_ceiling_is_stopped_and_named(
    db: Path, tmp_path: Path
) -> None:
    with _started(db, tmp_path, "--no-fetch", body="exec sleep 30\n", GOH_AUDIT_WALL="1") as (
        proc,
        _record,
    ):
        rc = proc.wait(timeout=patience(15))
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


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()


def test_a_refresh_fetches_from_upstream_not_the_copy_it_was_seeded_from(
    tmp_path: Path, monkeypatch
) -> None:
    """GOH.SHAREDADVISORY found it: the copy cloned `--local` from `~/.cargo/advisory-db` kept that
    clone as `origin`, and cargo-audit fetches through `origin`, so every refresh re-read the stale
    seed (1509 advisories, two days behind; RUSTSEC-2026-0543 unseen). A seeded copy, and an old
    one, point `origin` at the upstream before a refresh."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "lib"))
    import advisory_db

    seed = tmp_path / "seed"
    fast_init(seed)
    _git(
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "s",
        cwd=seed,
    )
    monkeypatch.setattr(advisory_db, "SHARED", seed)
    db = tmp_path / "cache" / "advisory-db"
    monkeypatch.setenv("GOH_ADVISORY_DB", str(db))
    with advisory_db.held(fetch=False):
        pass
    assert _git("remote", "get-url", "origin", cwd=db) == advisory_db.UPSTREAM
    # an old copy, seeded before the fix: repaired by the next refresh
    _git("remote", "set-url", "origin", str(seed), cwd=db)
    with advisory_db.held(fetch=True):
        pass
    assert _git("remote", "get-url", "origin", cwd=db) == advisory_db.UPSTREAM


# GOH.AUDITONCE (2026-11-17). A consumer's own audit step went through this wrapper, not through
# rust_gate.sh, so it never saw goh's shared rulings and refused the v0.130.0 pin pushes on
# RUSTSEC-2026-0543 (chrono via rmcp). The wrapper now applies them per lock, exactly as the gate:
# an ignore only where the blocker still reaches the advised crate. The fake cargo fails unless it
# was told to ignore the advisory, which is what a real audit of such a lock does.
CHRONO = "RUSTSEC-2026-0543"
GRUDGING = f'for a in "$@"; do [ "$a" = {CHRONO} ] && exit 0; done\necho "{CHRONO}" >&2\nexit 1\n'


def _pkg(name: str, *deps: str) -> str:
    listed = "".join(f' "{d}",\n' for d in deps)
    return f'\n[[package]]\nname = "{name}"\nversion = "1.0.0"\n' + (
        f"dependencies = [\n{listed}]\n" if deps else ""
    )


def _audit_lock(tmp_path: Path, db: Path, lock: str, *, flag: str = "--file") -> tuple[int, str]:
    (tmp_path / "Cargo.lock").write_text("version = 4\n" + lock)
    fake = tmp_path / "bin" / "cargo"
    fake.parent.mkdir(exist_ok=True)
    fake.write_text("#!/bin/sh\n" + GRUDGING)
    fake.chmod(0o755)
    env = hermetic_env(GOH_ADVISORY_DB=str(db))
    env["PATH"] = f"{fake.parent}:{env['PATH']}"
    where = [flag, str(tmp_path / "Cargo.lock")] if flag else []
    r = subprocess.run(
        ["python3", str(SEAM), "run", "--no-fetch", "--", "cargo", "audit", *where],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=patience(30),
    )
    return r.returncode, r.stderr


@pytest.mark.parametrize("flag", ["--file", "-f", ""])
def test_the_wrapper_applies_the_shared_ruling_where_the_blocker_reaches_the_crate(
    db: Path, tmp_path: Path, flag: str
) -> None:
    rc, err = _audit_lock(tmp_path, db, _pkg("rmcp", "chrono") + _pkg("chrono"), flag=flag)
    assert rc == 0, err


def test_the_wrapper_keeps_the_advisory_red_where_no_blocker_carries_it(
    db: Path, tmp_path: Path
) -> None:
    rc, err = _audit_lock(tmp_path, db, _pkg("app", "chrono") + _pkg("chrono"))
    assert rc == 1 and CHRONO in err, err


def test_the_wrapper_names_a_ruling_whose_blocker_dropped_the_crate(
    db: Path, tmp_path: Path
) -> None:
    rc, err = _audit_lock(tmp_path, db, _pkg("rmcp") + _pkg("app", "chrono") + _pkg("chrono"))
    assert rc == 1 and "no longer depends on chrono" in err, err
