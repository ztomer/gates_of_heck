"""The lock order's three nesting gaps GOH.LOCKORDER left (GOH.LOCKNEST, 2026-11-15).

* A 0-spinner `load.py` run inside a holder of the load slot is that holder's: it takes no second
  place (and never waits on its own parent for one) -- the table's "load" row said so, and
  load.py did not.
* The gate slot structural.sh takes FOR a consumer's `tools/gate.sh` is held by that gate.sh, so
  every later child of the gate.sh is under it: the lock order sees it there too, though no
  environment variable could reach those children from structural.sh.
* A `repo` lock (lib/lock_order.tsv's scope column) of repository B is not ordered against a lock
  held in repository A: `.loop`'s commit gate held the gate slot, and its test's fixture ship was
  refused waiting on the FIXTURE repo's own land lock (scrubbed by hand in .loop 0b7e419).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from test_gate_slot_consumer import Consumer, _cenv
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import lock_order
import pytest

ME = os.getpid()
LOAD = REPO_ROOT / "tools" / "load.py"


def _fresh(**extra: str) -> dict[str, str]:
    """This suite's environment, minus the locks the suite's own gate holds (a fresh top level)."""
    return hermetic_env(NO_COLOR="1", **extra)  # GOH_HELD_LOCKS is a GOH_ key: dropped


def test_a_nested_zero_spinner_run_shares_its_parents_place_in_the_slot(tmp_path) -> None:
    """One place in the shared slot (GOH_LOAD_GATES=1), taken by the outer run: the inner run is
    the outer's, so it neither waits for a second place nor removes the outer's when it ends."""
    gates = Path(os.environ["GOH_LOAD_DIR"]) / "gates"
    inner = (f'"{sys.executable}" "{LOAD}" --background-ok test --wait-max 2s 0 -- echo INNER-RAN; '
             f'echo "inner-rc=$?"; ls "{gates}"')  # fmt: skip
    done = subprocess.run(
        [sys.executable, str(LOAD), "--background-ok", "test", "0", "--", "bash", "-c", inner],
        capture_output=True, text=True, env=_fresh(GOH_LOAD_GATES="1"), stdin=subprocess.DEVNULL,
        check=False, timeout=patience(120),
    )  # fmt: skip
    assert done.returncode == 0, done
    assert "INNER-RAN" in done.stdout and "inner-rc=0" in done.stdout, done
    assert "inside the load slot of pid" in done.stderr + done.stdout, done
    outer = [ln for ln in done.stdout.splitlines() if ln.endswith(".json")]
    assert len(outer) == 1, done.stdout  # the outer's own place, still there after the inner ended


@pytest.mark.load_sensitive
def test_a_zero_spinner_run_with_a_stray_load_mark_still_takes_its_place(tmp_path) -> None:
    """A mark naming a live process that is NOT an ancestor (exported by hand, inherited by a
    detached process) shares nothing: the run takes its own place."""
    stray = subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL)
    try:
        env = _fresh(GOH_LOAD_GATES="1", **{lock_order.ENV: f"load@{stray.pid}"})
        done = subprocess.run(
            [sys.executable, str(LOAD), "--background-ok", "test", "0", "--", "true"],
            capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, check=False,
            timeout=patience(120),
        )  # fmt: skip
    finally:
        stray.kill()
        stray.wait()
    assert done.returncode == 0, done
    assert "inside the load slot" not in done.stderr + done.stdout, done


CHECK = (
    f'rc=0; "{sys.executable}" -c "import sys; sys.path.insert(0, \'{REPO_ROOT}/lib\'); '
    f"import lock_order; sys.exit(lock_order.main(['check', '{{lock}}']))\" || rc=$?; "
    f'echo "check-{{lock}}-rc=$rc"'
)  # fmt: skip  (the gate runs under set -e)


@pytest.mark.load_sensitive
def test_a_consumer_gate_shs_later_children_are_under_the_slot_taken_for_it(tmp_path, goh):
    """structural.sh --full takes the slot for its parent gate.sh; the gate.sh's own layer, run
    after structural.sh ended, waits on nothing ranked below it."""
    env = _cenv(tmp_path, goh)
    env.pop(lock_order.ENV, None)
    layer = "; ".join(CHECK.format(lock=name) for name in ("load", "land", "container"))
    gate = Consumer(tmp_path, "layer", env, layer=layer)
    try:
        assert gate.proc.wait(timeout=patience(180)) == 0, gate.out()
        out = gate.out()
        assert "gate slot 1/1 taken" in out, out
        assert "check-load-rc=78" in out and "check-land-rc=78" in out, out
        assert "'gate_slot' (rank 50, held by pid" in out, out
        assert "check-container-rc=0" in out, out  # ranked above the slot: in order
    finally:
        gate.kill()


def _repo(path: Path) -> Path:
    path.mkdir()
    fast_init(path, "main")
    return path


def _sh(script: str, cwd: Path, held: str = "") -> subprocess.CompletedProcess[str]:
    env = _fresh(**({lock_order.ENV: held} if held else {}))
    return subprocess.run(["bash", "-c", f'. "{REPO_ROOT}/lib/lock_order.sh" || exit 9; {script}'],
                          capture_output=True, text=True, env=env, cwd=cwd,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def test_a_repo_lock_of_another_repository_is_not_ordered_against_the_held_set(tmp_path) -> None:
    """The .loop shape: a gate in repo A holds the gate slot; a fixture's land in repo B waits on
    B's own land lock, which no holder of A's slot can be waiting for."""
    a, b = _repo(tmp_path / "a"), _repo(tmp_path / "b")
    take = (f'goh_lock_mark gate_slot && cd "{b}" && exec 7>>.git/goh-land.lock && '
            f'goh_lock_take land 7 && echo TOOK')  # fmt: skip
    done = _sh(take, a)
    assert done.returncode == 0 and "TOOK" in done.stdout, done
    same = _sh("goh_lock_mark gate_slot && exec 7>>.git/goh-land.lock && goh_lock_take land 7", a)
    assert same.returncode == lock_order.EXIT_ORDER, same  # its own repository's: refused
    assert "'gate_slot' (rank 50" in same.stderr, same.stderr


def test_the_scope_is_the_repository_not_the_checkout(tmp_path) -> None:
    """Every worktree of one repository shares its land lock, so a mark taken in a worktree orders
    the main checkout's land lock: the scope is the common git dir."""
    a = _repo(tmp_path / "a")
    who = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}  # fmt: skip
    subprocess.run(["git", "-C", str(a), "commit", "-q", "--allow-empty", "-m", "x"], check=True,
                   capture_output=True, env=_fresh(**who), stdin=subprocess.DEVNULL)  # fmt: skip
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(a), "worktree", "add", "-q", "--detach", str(wt)], check=True,
                   capture_output=True, stdin=subprocess.DEVNULL)  # fmt: skip
    assert (
        lock_order.repo_key(wt) == lock_order.repo_key(a) == lock_order.repo_key(a / ".git" / "x")
    )
    assert lock_order.repo_key(tmp_path) == ""  # in no repository: no scope
    held = {lock_order.ENV: f"gate_slot@{ME}:{lock_order.repo_key(wt)}"}
    assert lock_order.refusal("land", held, where=a) is not None
    assert lock_order.refusal("land", held, where=_repo(tmp_path / "b")) is None


def test_an_unscoped_mark_and_a_machine_lock_stay_ordered(tmp_path) -> None:
    """Only a `repo` lock is scoped, and only against a mark that names another repository: a mark
    from no known repository (or an older goh) orders everything, as before."""
    a, b = _repo(tmp_path / "a"), _repo(tmp_path / "b")
    key_a = lock_order.repo_key(a)
    assert lock_order.refusal("land", {lock_order.ENV: f"gate_slot@{ME}"}, where=b) is not None
    assert lock_order.refusal("load", {lock_order.ENV: f"gate_slot@{ME}:{key_a}"}, where=b)
    assert lock_order.with_held("load", ME, {}, where=a) == f"load@{ME}:{key_a}"
