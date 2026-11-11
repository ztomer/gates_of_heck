"""`tools/session_bench.py`: how much N concurrent sessions' gates serialize on each other.

The 2026-10-06 suite ran at load 12-31 beside other sessions' gates and no number from it was a
measurement. What one session costs the next is a property of the shared locks (the tree lock,
cargo's build-dir and package-cache locks, the HEAD export, the desktop lock) and of the CPU, and
nothing measured it. The bench runs one workload in N isolated checkouts at once, for each N, and
reports makespan, speedup over N serial runs, the Universal Scalability Law's contention (sigma)
and coherency (kappa), every lock wait it saw by name, and the steps that inflated most.

Pinned with workloads whose answer is known: one that holds a shared flock for its whole run
(fully serial: speedup ~1, sigma ~1), and one that only sleeps (fully parallel: speedup ~N).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

BENCH = REPO_ROOT / "tools" / "session_bench.py"

HOLD = 0.2  # seconds SERIAL holds the shared lock (end to end only; no ratio is asserted)
SERIAL = (
    "python3 -c \"import fcntl,time,os; f=open(os.environ['SHARED_LOCK'],'a'); "
    f'fcntl.flock(f, fcntl.LOCK_EX); time.sleep({HOLD})"'
)
PARALLEL = f"sleep {HOLD}"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("x\n")
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, stdin=subprocess.DEVNULL)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    return repo


def _run(tmp_path: Path, workload: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BENCH), "--workload", workload, *args, str(_repo(tmp_path))],
        capture_output=True,
        text=True,
        env=hermetic_env(SHARED_LOCK=str(tmp_path / "shared.lock"), TMPDIR=str(tmp_path)),
        timeout=120,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _bench(tmp_path: Path, workload: str, *args: str, rc: int = 0) -> dict:
    out = tmp_path / "bench.json"
    r = subprocess.run(
        [
            sys.executable,
            str(BENCH),
            "--workload",
            workload,
            "--json",
            str(out),
            *args,
            str(_repo(tmp_path)),
        ],
        capture_output=True,
        text=True,
        env=hermetic_env(SHARED_LOCK=str(tmp_path / "shared.lock")),
        timeout=120,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == rc, r.stdout + r.stderr
    return json.loads(out.read_text())


def _row(doc: dict, n: int) -> dict:
    return next(row for row in doc["rows"] if row["sessions"] == n)


def _scored(makespans: dict[int, float]) -> tuple[list[dict], dict[str, float]]:
    rows = [{"sessions": n, "makespan_s": t} for n, t in sorted(makespans.items())]
    return rows, _bench_module().score(rows)


def test_a_workload_holding_a_shared_lock_reads_as_serial() -> None:
    """STRUCTURAL, not timed (GOH.ENVLEAK): a workload with a parallel start s and a serial hold
    L takes s + N*L for N sessions, so its true serialized fraction is L / t1. Fed those
    makespans, the bench's scoring must recover exactly that fraction; a blind fit (sigma ~0) or
    a halved one fails. This used to run a real flock and read 0.615 against a truth of 0.842 on
    a loaded macOS runner under coverage (goh run 43039999613)."""
    for s, hold in ((0.0, 1.0), (0.2, 1.0), (0.5, 0.4)):
        rows, usl = _scored({n: s + n * hold for n in (1, 2, 4)})
        truth = hold / rows[0]["makespan_s"]
        assert abs(usl["sigma"] - truth) < 0.02 and usl["kappa"] < 0.02, (s, truth, usl)
        assert rows[2]["speedup"] < 4 / (1 + 3 * 0.8 * truth), (truth, rows)


def test_a_workload_sharing_nothing_reads_as_parallel() -> None:
    """STRUCTURAL: equal makespans at every N are a linear speedup and no contention."""
    rows, usl = _scored({1: 1.0, 2: 1.0, 4: 1.0})
    assert [r["speedup"] for r in rows] == [1.0, 2.0, 4.0], rows
    assert [r["efficiency"] for r in rows] == [1.0, 1.0, 1.0], rows
    assert usl == {"sigma": 0.0, "kappa": 0.0}, usl


def test_the_bench_scores_its_own_measured_makespans(tmp_path: Path) -> None:
    """End to end with no clock ratio in the assertion: the real bench's speedups and fit are
    exactly the scoring of the makespans it reports, whatever the box's load."""
    doc = _bench(tmp_path, SERIAL, "--sessions", "1,2", "--warmup", "0")
    rows = [{"sessions": r["sessions"], "makespan_s": r["makespan_s"]} for r in doc["rows"]]
    assert _bench_module().score(rows) == doc["usl"], doc
    assert [r["speedup"] for r in rows] == [r["speedup"] for r in doc["rows"]], doc


def test_every_lock_wait_is_named(tmp_path: Path) -> None:
    say = (
        "echo 'Blocking waiting for file lock on build directory' >&2; "
        "echo '→ the build tree is held by pid 1' >&2; "
        "echo '→ the desktop is held by someone' >&2"
    )
    doc = _bench(tmp_path, say, "--sessions", "1,2", "--warmup", "0")
    waits = _row(doc, 2)["lock_waits"]
    assert waits == {"cargo: build directory": 2, "tree lock": 2, "desktop lock": 2}, waits


def test_each_session_runs_in_its_own_checkout(tmp_path: Path) -> None:
    """Isolated means isolated: a session sharing a checkout with another would meet the tree
    lock, and the bench would measure that instead of the machine."""
    doc = _bench(tmp_path, 'pwd >> "$SHARED_LOCK"', "--sessions", "3", "--warmup", "0")
    dirs = (tmp_path / "shared.lock").read_text().split()
    assert len(set(dirs)) == 3 and not any(d == str(tmp_path / "repo") for d in dirs), dirs
    assert _row(doc, 3)["failed"] == 0


def test_a_failing_session_is_counted_not_timed_as_a_success(tmp_path: Path) -> None:
    """A bench over failing runs is not a measurement: counted, and the run exits 1."""
    doc = _bench(tmp_path, "exit 3", "--sessions", "2", "--warmup", "0", rc=1)
    assert _row(doc, 2)["failed"] == 2, doc


def _timings(path: Path, rows: list[tuple[str, float]]) -> Path:
    path.write_text("".join(json.dumps({"label": k, "ms": v, "rc": 0}) + "\n" for k, v in rows))
    return path


def _bench_module():
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import session_bench
    finally:
        sys.path.remove(str(REPO_ROOT / "tools"))
    return session_bench


def test_the_slowest_inflating_steps_are_reported(tmp_path: Path) -> None:
    """The report names the step whose time grew most from one session to many. STRUCTURAL, not
    timed (GOH.WALLCLOCK): the durations are injected as GOH_TIMINGS lines, so the ordering, the
    mean, the ratio and the exclusions are tested exactly. This test used to sleep 0.3 s under a
    flock and assert ratio > 1.5 on the measured walls; on a hosted runner it read 1.48 (goh run
    42990295293) -- a wall-clock assertion on a loaded box. That the serialization IS measured
    stays with the SERIAL/PARALLEL pins above, which bound by the box's own t1; nothing here
    depends on a clock."""
    sb = _bench_module()
    alone = sb.step_means(
        [
            _timings(
                tmp_path / "a0",
                [("locked", 300), ("free", 5), ("zero", 0), ("alone only", 9), ("gain", 100)],
            ),
            _timings(tmp_path / "a1", [("locked", 500), ("gain", 100)]),
            tmp_path / "missing",  # a session that wrote no timings is skipped, not an error
        ]
    )
    crowded = sb.step_means(
        [
            _timings(
                tmp_path / "c0",
                [("locked", 900), ("free", 5), ("zero", 40), ("crowded only", 70), ("gain", 130)],
            ),
            _timings(tmp_path / "c1", [("locked", 1100)]),
        ]
    )
    assert alone["locked"] == 400 and crowded["locked"] == 1000, (alone, crowded)
    rows = sb.inflation(alone, crowded)
    assert [r["label"] for r in rows] == ["locked", "zero", "gain", "free"], rows
    assert rows[0] == {"label": "locked", "alone_ms": 400.0, "crowded_ms": 1000.0, "ratio": 2.5}
    assert next(r for r in rows if r["label"] == "zero")["ratio"] is None  # no divide by zero
    assert next(r for r in rows if r["label"] == "free")["ratio"] == 1.0


def test_the_bench_reads_the_workloads_timings_into_its_report(tmp_path: Path) -> None:
    """End to end through the real bench, with no clock in the assertion: a workload that writes
    GOH_TIMINGS lines of FIXED values gets exactly those steps, with exactly those means, in the
    report, whatever the box's load."""
    emit = (
        "python3 -c \"import os,json; f=open(os.environ['GOH_TIMINGS'],'a'); "
        "f.write(json.dumps({'label':'fixed','ms':250,'rc':0})+chr(10)); "
        "f.write(json.dumps({'label':'other','ms':5,'rc':0})+chr(10))\""
    )
    doc = _bench(tmp_path, emit, "--sessions", "1,3", "--warmup", "0")
    assert {r["label"]: (r["alone_ms"], r["crowded_ms"]) for r in doc["inflation"]} == {
        "fixed": (250.0, 250.0),
        "other": (5.0, 5.0),
    }, doc["inflation"]


FAILS = "echo the cause of it; exit 3"


def test_a_failed_warm_up_shows_its_cause_and_keeps_its_log(tmp_path: Path) -> None:
    """The first Phase 4 bench failed in its warm-up, named the log, and then deleted it with the
    work dir (2026-10-08): the cause was gone. A failure prints the log's tail and keeps it."""
    r = _run(tmp_path, FAILS, "--sessions", "1", "--warmup", "1")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "the cause of it" in r.stderr, r.stderr
    kept = list(tmp_path.glob("goh-session-bench.*/logs/warm0-c0-s0.log"))
    assert len(kept) == 1, r.stderr


def test_a_failed_row_shows_its_cause(tmp_path: Path) -> None:
    """The same for a row's failed sessions, which were kept but never shown."""
    r = _run(tmp_path, FAILS, "--sessions", "2", "--warmup", "0")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "the cause of it" in r.stderr, r.stderr


def test_the_quiet_threshold_is_quiet_sh_s() -> None:
    """The bench's 'not a quiet box' warning and tools/quiet.sh judge one box by one number."""
    ours = re.search(r"^QUIET_LOAD = ([0-9.]+)$", BENCH.read_text(), re.MULTILINE)
    theirs = re.search(
        r"^max_load=([0-9.]+) ", (REPO_ROOT / "tools" / "quiet.sh").read_text(), re.MULTILINE
    )
    assert ours and theirs and float(ours[1]) == float(theirs[1]), (ours, theirs)
