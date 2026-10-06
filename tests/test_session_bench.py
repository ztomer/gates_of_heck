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
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

BENCH = REPO_ROOT / "tools" / "session_bench.py"

SERIAL = (
    "python3 -c \"import fcntl,time,os; f=open(os.environ['SHARED_LOCK'],'a'); "
    'fcntl.flock(f, fcntl.LOCK_EX); time.sleep(0.4)"'
)
PARALLEL = "sleep 0.4"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("x\n")
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"],
        check=True,
    )
    return repo


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
    )
    assert r.returncode == rc, r.stdout + r.stderr
    return json.loads(out.read_text())


def _row(doc: dict, n: int) -> dict:
    return next(row for row in doc["rows"] if row["sessions"] == n)


def test_a_workload_holding_a_shared_lock_reads_as_serial(tmp_path: Path) -> None:
    doc = _bench(tmp_path, SERIAL, "--sessions", "1,2,4", "--warmup", "0")
    assert _row(doc, 4)["speedup"] < 1.4, doc
    assert doc["usl"]["sigma"] > 0.7, doc


def test_a_workload_sharing_nothing_reads_as_parallel(tmp_path: Path) -> None:
    doc = _bench(tmp_path, PARALLEL, "--sessions", "1,2,4", "--warmup", "0")
    assert _row(doc, 4)["speedup"] > 3.0, doc
    assert doc["usl"]["sigma"] < 0.15, doc


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


def test_the_slowest_inflating_steps_are_reported(tmp_path: Path) -> None:
    """With GOH_TIMINGS lines from the workload, the bench names the step whose time grew most
    from one session to many: where the serialization IS, not only that it exists."""
    timed = (
        "python3 -c \"import fcntl,time,os,json; f=open(os.environ['SHARED_LOCK'],'a'); "
        "t=time.time(); fcntl.flock(f, fcntl.LOCK_EX); time.sleep(0.3); "
        "open(os.environ['GOH_TIMINGS'],'a').write(json.dumps({'label':'locked step',"
        "'ms':int((time.time()-t)*1000),'rc':0})+chr(10)); "
        "open(os.environ['GOH_TIMINGS'],'a').write(json.dumps({'label':'free step','ms':5,'rc':0})+chr(10))\""
    )
    doc = _bench(tmp_path, timed, "--sessions", "1,3", "--warmup", "0")
    top = doc["inflation"][0]
    assert top["label"] == "locked step" and top["ratio"] > 1.5, doc["inflation"]
