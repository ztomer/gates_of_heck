"""checks/check_lock_order.py, calibrated: goh's tree sorts, and today's order re-planted is red.

The 2026-11-15 deadlock was a ship holding the land lock waiting for the load slot, and a land
holding the load slot (run inside `tools/load.py`) waiting for the land lock. The check reads this
tree green, then reads it again with the land-inside-the-slot invocation planted (its `plant` seam:
a file read as if tracked, so no copy of goh is made): it must name the cycle, land -> load -> land.

Those two read the whole tree. A four-line plant is judged beside the four real files its edge runs
through (`small`), not the other 900 (GOH.PYSLOWTESTS: 14 whole-tree scans, 189 s of a run).
"""
# goh-land-guard: test_the_clean_tree_has_no_finding

from __future__ import annotations

import subprocess
import sys

import pytest
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "checks"))
import check_lock_order

CHECK = REPO_ROOT / "checks" / "check_lock_order.py"
LAND_IN_SLOT = (
    '#!/usr/bin/env bash\n"$GOH_PY" "$GOH/tools/load.py" --background-ok "land gate" '
    '--max 90m 0 -- bash "$GOH/tools/land.sh"\n'
)

# The tree lock, the land lock, and what a land runs: what every edge planted below is made of.
SMALL = ["lib/tree_lock.sh", "tools/land.sh", "tools/landq.py", "lib/land_lock.py"]


@pytest.fixture
def small(monkeypatch) -> None:
    """The check reads SMALL of goh's tree, and the plant."""
    monkeypatch.setattr(check_lock_order, "_tracked", lambda root: list(SMALL))


def test_the_clean_tree_has_no_finding() -> None:
    """The whole tree, through the command line: no finding (exit 0), and the order it sorts in."""
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


@pytest.mark.usefixtures("small")
def test_a_raw_flock_is_red() -> None:
    plant = {"lib/stray_lock.py": "import fcntl\n\n\ndef take(f):\n    fcntl.flock(f, 2)\n"}
    findings, _ = check_lock_order.check(REPO_ROOT, plant)
    assert any("lib/stray_lock.py:5: a raw flock" in f for f in findings), findings


@pytest.mark.usefixtures("small")
def test_a_lock_the_table_does_not_list_there_is_red() -> None:
    body = 'import lock_order\n\n\ndef take(f):\n    lock_order.flock(f, "tree")\n'
    plant = {"lib/stray_lock.py": body}
    findings, _ = check_lock_order.check(REPO_ROOT, plant)
    want = "lib/stray_lock.py: acquires 'tree', but lib/lock_order.tsv does not list it"
    assert any(want in f for f in findings), findings


@pytest.mark.usefixtures("small")
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


@pytest.mark.usefixtures("small")
def test_the_small_tree_has_no_inversion_of_its_own() -> None:
    """What the plants below are judged against: the edges are theirs."""
    findings, order = check_lock_order.check(REPO_ROOT)
    assert [f for f in findings if "does not acquire it" not in f] == []
    assert {"tree", "land"} <= set(order)
    assert _findings("") == []


def test_a_module_name_in_a_string_is_no_reference_but_a_call_is() -> None:
    """GOH.LOCKORDERWORD: a word in a string (a record key `"ship.stage("`, a label) read as a
    reference to tools/ship.py and reported a false cycle; the lane renamed its keys to pass."""
    names = {"ship.py": ["tools/ship.py"]}
    assert check_lock_order._mentions('label = "ship.stage(x)"', names) == set()
    assert check_lock_order._mentions("label = 'from ship'", names) == set()
    assert check_lock_order._mentions("ship.stage(x)", names) == {"tools/ship.py"}  # calibration
    assert check_lock_order._mentions("from ship import x", names) == {"tools/ship.py"}
    assert check_lock_order._mentions('importlib.import_module("ship")', names) == {"tools/ship.py"}


# ── through bin/goh, and no lock into a detached child (GOH.LOCKORDERBIN2) ─────────────────
# checks/check_lock_order.py follows a call through bin/goh, and no lock into a detached child
# (GOH.LOCKORDERBIN2).
#
# A gate holding a lock that runs `goh land` takes the land lock as surely as one that runs
# tools/landq.py: `goh land` IS tools/landq.py (crates/goh/house_scripts.tsv), and the checker saw
# the second only. Mapping the subcommands exposed the opposite blind spot: gates/ruff_house.py runs
# `goh land --ruff-bump --detach`, a child in a session of its own that holds none of the caller's
# locks, and the checker read it as the caller's land acquisition. A detached call (`--detach`,
# `setsid`, `start_new_session=True`) forms no edge.

TREE = (REPO_ROOT / "lib" / "tree_lock.sh").read_text()
INVERSION = "'tree' (rank 60) is held while 'land' (rank 20) is taken"


def _findings(tail: str) -> list[str]:
    findings, _ = check_lock_order.check(REPO_ROOT, {"lib/tree_lock.sh": TREE + tail})
    return [f for f in findings if INVERSION in f]


@pytest.mark.usefixtures("small")
def test_landq_by_path_is_the_baseline_edge() -> None:
    assert _findings('\n"$GOH_PY" "$GOH/tools/landq.py" --enqueue\n')


@pytest.mark.usefixtures("small")
def test_bin_goh_land_forms_the_same_edge_as_landq() -> None:
    for call in ('\n"$GOH/bin/goh" land --enqueue\n', "\ngoh land --wait X\n",
                 '\n"$GOH/bin/goh" landq --report\n'):  # fmt: skip
        got = _findings(call)
        assert got and "tools/landq.py" in got[0], (call, got)


@pytest.mark.usefixtures("small")
def test_a_python_argv_through_goh_forms_the_edge() -> None:
    body = ('import lock_order\n\n\ndef run(goh, f):\n    lock_order.flock(f, "tree")\n'
            '    subprocess.run([goh, "land", "--enqueue"], check=False)\n')  # fmt: skip
    findings, _ = check_lock_order.check(REPO_ROOT, {"lib/tree_lock_py.py": body})
    assert any(INVERSION in f and "tools/landq.py" in f for f in findings), findings


@pytest.mark.usefixtures("small")
def test_a_detached_call_forms_no_edge() -> None:
    for call in ('\n"$GOH/bin/goh" land --ruff-bump --detach\n',
                 '\nsetsid "$GOH_PY" "$GOH/tools/landq.py" --enqueue &\n'):  # fmt: skip
        assert _findings(call) == [], call
    body = ('import lock_order\n\n\ndef run(goh, f):\n    lock_order.flock(f, "tree")\n'
            '    subprocess.Popen(\n        [goh, "land", "--enqueue"],\n'
            '        start_new_session=True,\n    )\n')  # fmt: skip
    findings, _ = check_lock_order.check(REPO_ROOT, {"lib/tree_lock_py.py": body})
    assert not [f for f in findings if INVERSION in f], findings
