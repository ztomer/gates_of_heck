"""checks/check_lock_order.py, calibrated: goh's tree sorts, and today's order re-planted is red.

The 2026-11-15 deadlock was a ship holding the land lock waiting for the load slot, and a land
holding the load slot (run inside `tools/load.py`) waiting for the land lock. The check reads this
tree green, then reads it again with the land-inside-the-slot invocation planted (its `plant` seam:
a file read as if tracked, so no copy of goh is made): it must name the cycle, land -> load -> land.
"""

from __future__ import annotations

import subprocess
import sys

from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "checks"))
import check_lock_order

CHECK = REPO_ROOT / "checks" / "check_lock_order.py"
LAND_IN_SLOT = (
    '#!/usr/bin/env bash\n"$GOH_PY" "$GOH/tools/load.py" --background-ok "land gate" '
    '--max 90m 0 -- bash "$GOH/tools/land.sh"\n'
)


def test_this_tree_sorts_in_the_table_order() -> None:
    done = subprocess.run([sys.executable, str(CHECK), str(REPO_ROOT)], capture_output=True,
                          text=True, env=hermetic_env(drop_git=True), stdin=subprocess.DEVNULL,
                          check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr
    order = done.stdout.split("sorts: ")[1].split(" < ")
    assert order.index("ship") < order.index("land") < order.index("load"), done.stdout


def test_the_replanted_2026_11_15_order_is_red() -> None:
    findings, _ = check_lock_order.check(REPO_ROOT, {"tools/land_in_slot.sh": LAND_IN_SLOT})
    said = "\n".join(findings)
    # a 0-spinner run holds a gate slot for its command now (GOH.ONEADMISSION): the cycle is
    # through it, and the load slot load.py may hold for an experiment is still against the order
    assert "a lock cycle -- gate_slot -> land -> gate_slot" in said, said
    assert "'load' (rank 30) is held while 'land' (rank 20) is taken" in said, said
    assert "tools/land.sh" in said and "tools/load.py" in said, said


def test_a_raw_flock_is_red() -> None:
    plant = {"lib/stray_lock.py": "import fcntl\n\n\ndef take(f):\n    fcntl.flock(f, 2)\n"}
    findings, _ = check_lock_order.check(REPO_ROOT, plant)
    assert any("lib/stray_lock.py:5: a raw flock" in f for f in findings), findings


def test_a_lock_the_table_does_not_list_there_is_red() -> None:
    body = 'import lock_order\n\n\ndef take(f):\n    lock_order.flock(f, "tree")\n'
    plant = {"lib/stray_lock.py": body}
    findings, _ = check_lock_order.check(REPO_ROOT, plant)
    want = "lib/stray_lock.py: acquires 'tree', but lib/lock_order.tsv does not list it"
    assert any(want in f for f in findings), findings


def test_a_rank_inversion_is_red_even_without_a_cycle() -> None:
    """The tree lock (rank 60) held while a script that takes the land lock (20) runs."""
    text = (REPO_ROOT / "lib" / "tree_lock.sh").read_text()
    findings, _ = check_lock_order.check(
        REPO_ROOT, {"lib/tree_lock.sh": text + '\nbash "$GOH/tools/land.sh"\n'}
    )
    assert any("'tree' (rank 60) is held while 'land' (rank 20) is taken" in f for f in findings), (
        findings
    )


def test_a_tree_with_no_lock_table_is_refused_not_passed(tmp_path) -> None:
    done = subprocess.run([sys.executable, str(CHECK), str(tmp_path)], capture_output=True,
                          text=True, env=hermetic_env(drop_git=True), stdin=subprocess.DEVNULL,
                          check=False)  # fmt: skip
    assert done.returncode == 2 and "no lib/lock_order.tsv" in done.stderr, done


def test_the_clean_tree_has_no_finding() -> None:
    assert check_lock_order.check(REPO_ROOT)[0] == []
