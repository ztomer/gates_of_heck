"""lib/lock_order.py and .sh: a wait is refused out of the table's order (GOH.LOCKORDER).

Lockdep's rule: a process may wait on a lock only when its rank is above every lock it or an
ancestor holds (GOH_HELD_LOCKS, `name@pid`). The refusal is deterministic -- it does not depend on
whether the lock is free -- and names both locks and the table. A try-lock never waits, so it is
never refused; a dead holder's entry guards nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "lib"))
import lock_order

ME = os.getpid()


def test_the_table_is_ranked_once_per_lock_and_names_real_files() -> None:
    table = lock_order.table()
    ranks = [lock.rank for lock in table.values()]
    assert len(set(ranks)) == len(ranks), ranks
    assert table["ship"].rank < table["land"].rank < table["load"].rank < table["gate_slot"].rank
    for lock in table.values():
        for rel in lock.files:
            assert (REPO_ROOT / rel).is_file(), (lock.name, rel)


def test_a_wait_below_a_held_lock_is_refused_naming_both() -> None:
    env = {lock_order.ENV: f"load@{ME}"}
    why = lock_order.refusal("land", env)
    assert why is not None
    assert "'land' (rank 20" in why and "'load' (rank 30, held by pid" in why, why
    assert str(lock_order.TABLE) in why, why


def test_a_wait_above_every_held_lock_is_allowed() -> None:
    assert lock_order.refusal("load", {lock_order.ENV: f"ship@{ME},land@{ME}"}) is None
    assert lock_order.refusal("land", {}) is None


def test_a_reentrant_lock_held_by_an_ancestor_is_its_own_rule_not_a_refusal() -> None:
    assert lock_order.refusal("land", {lock_order.ENV: f"land@{ME}"}) is None
    assert lock_order.refusal("tree", {lock_order.ENV: f"tree@{ME}"}) is not None  # not re-entrant


def test_a_dead_holders_entry_guards_nothing() -> None:
    dead = subprocess.Popen([sys.executable, "-c", ""], stdin=subprocess.DEVNULL)
    dead.wait()
    assert lock_order.refusal("land", {lock_order.ENV: f"load@{dead.pid}"}) is None


def test_an_unknown_lock_is_refused() -> None:
    assert "not in" in (lock_order.refusal("nope", {}) or "")


def test_flock_refuses_the_wait_before_touching_the_lock(tmp_path, monkeypatch) -> None:
    """Deterministic: the lock is free, and the out-of-order wait is refused all the same."""
    monkeypatch.setenv(lock_order.ENV, f"load@{ME}")
    with (tmp_path / "l").open("w") as f:
        with pytest.raises(lock_order.OrderError, match="refusing to wait on 'land'"):
            lock_order.flock(f, "land")
        lock_order.flock(f, "land", wait=False)  # a try-lock never waits: allowed
        lock_order.unlock(f)


def test_holding_exports_the_lock_to_children_and_restores() -> None:
    before = os.environ.get(lock_order.ENV)
    with lock_order.holding("land"):
        show = f"import os; print(os.environ['{lock_order.ENV}'])"
        seen = subprocess.run([sys.executable, "-c", show],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              check=True).stdout.strip()  # fmt: skip
    assert seen.endswith(f"land@{ME}"), seen
    assert os.environ.get(lock_order.ENV) == before


def _sh(script: str, held: str = "") -> subprocess.CompletedProcess[str]:
    env = hermetic_env(**({lock_order.ENV: held} if held else {}))
    return subprocess.run(["bash", "-c", f'. "{REPO_ROOT}/lib/lock_order.sh" || exit 9; {script}'],
                          capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
                          check=False)  # fmt: skip


def test_the_shell_refuses_the_wait_and_exits_78(tmp_path) -> None:
    done = _sh(f'exec 7>>"{tmp_path}/l"; goh_lock_take land 7', held=f"load@{ME}")
    assert done.returncode == lock_order.EXIT_ORDER, done
    assert "refusing to wait on 'land'" in done.stderr, done.stderr


def test_the_shell_takes_in_order_and_marks_it_held(tmp_path) -> None:
    done = _sh(f'exec 7>>"{tmp_path}/l"; goh_lock_take land 7 && echo "$GOH_HELD_LOCKS"',
               held=f"ship@{ME}")  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().startswith(f"ship@{ME},land@"), done.stdout


def test_the_shell_try_lock_says_taken_with_1(tmp_path) -> None:
    lock = tmp_path / "l"
    with lock.open("w") as f:
        lock_order.flock(f, "tree", wait=False)
        done = _sh(f'exec 9>>"{lock}"; goh_lock_take tree 9 --nb')
    assert done.returncode == 1, done


def test_load_py_refuses_to_wait_for_the_slot_under_a_later_lock(tmp_path) -> None:
    env = hermetic_env(GOH_LOAD_DIR=str(tmp_path), **{lock_order.ENV: f"gate_slot@{ME}"})
    done = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "load.py"), "--background-ok",
                           "test", "0", "--", "true"], capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert done.returncode == 2, done  # load.py's "cannot take the slot"
    assert "refusing to wait on 'load'" in done.stderr and "'gate_slot'" in done.stderr, done.stderr


def test_a_released_lock_is_no_longer_held_for_the_next_child(tmp_path) -> None:
    """Adv360's keymap probe took the container lock, released it, then ran its whole gate again
    as a child, which takes it once more: the mark outlived the release, and the child was
    refused."""
    done = _sh(f'exec 8>>"{tmp_path}/l"; goh_lock_take container 8 && exec 8>&- && '
               f'goh_lock_drop container && bash -c \'. "{REPO_ROOT}/lib/lock_order.sh"; '
               f'exec 8>>"{tmp_path}/l"; goh_lock_take container 8\'')  # fmt: skip
    assert done.returncode == 0, done.stderr
