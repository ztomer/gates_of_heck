"""checks/check_lock_order.py follows a call through bin/goh, and no lock into a detached child
(GOH.LOCKORDERBIN2).

A gate holding a lock that runs `goh land` takes the land lock as surely as one that runs
tools/landq.py: `goh land` IS tools/landq.py (crates/goh/house_scripts.tsv), and the checker saw
the second only. Mapping the subcommands exposed the opposite blind spot: gates/ruff_house.py runs
`goh land --ruff-bump --detach`, a child in a session of its own that holds none of the caller's
locks, and the checker read it as the caller's land acquisition. A detached call (`--detach`,
`setsid`, `start_new_session=True`) forms no edge.
"""

from __future__ import annotations

import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "checks"))
import check_lock_order

TREE = (REPO_ROOT / "lib" / "tree_lock.sh").read_text()
INVERSION = "'tree' (rank 60) is held while 'land' (rank 20) is taken"


def _findings(tail: str) -> list[str]:
    findings, _ = check_lock_order.check(REPO_ROOT, {"lib/tree_lock.sh": TREE + tail})
    return [f for f in findings if INVERSION in f]


def test_landq_by_path_is_the_baseline_edge() -> None:
    assert _findings('\n"$GOH_PY" "$GOH/tools/landq.py" --enqueue\n')


def test_bin_goh_land_forms_the_same_edge_as_landq() -> None:
    for call in ('\n"$GOH/bin/goh" land --enqueue\n', "\ngoh land --wait X\n",
                 '\n"$GOH/bin/goh" landq --report\n'):  # fmt: skip
        got = _findings(call)
        assert got and "tools/landq.py" in got[0], (call, got)


def test_a_python_argv_through_goh_forms_the_edge() -> None:
    body = ('import lock_order\n\n\ndef run(goh, f):\n    lock_order.flock(f, "tree")\n'
            '    subprocess.run([goh, "land", "--enqueue"], check=False)\n')  # fmt: skip
    findings, _ = check_lock_order.check(REPO_ROOT, {"lib/tree_lock_py.py": body})
    assert any(INVERSION in f and "tools/landq.py" in f for f in findings), findings


def test_a_detached_call_forms_no_edge() -> None:
    for call in ('\n"$GOH/bin/goh" land --ruff-bump --detach\n',
                 '\nsetsid "$GOH_PY" "$GOH/tools/landq.py" --enqueue &\n'):  # fmt: skip
        assert _findings(call) == [], call
    body = ('import lock_order\n\n\ndef run(goh, f):\n    lock_order.flock(f, "tree")\n'
            '    subprocess.Popen(\n        [goh, "land", "--enqueue"],\n'
            '        start_new_session=True,\n    )\n')  # fmt: skip
    findings, _ = check_lock_order.check(REPO_ROOT, {"lib/tree_lock_py.py": body})
    assert not [f for f in findings if INVERSION in f], findings
