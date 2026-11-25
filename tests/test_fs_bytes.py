"""The bytes each test leaves written are read beside its file events (tests/_fs_bytes.py).

GOH.FSEVQUIET: an event count is blind to size, so a cut judged on events alone could write more.
The instrument is pinned on what it could miss: a child's write (100 MB through `dd` by hand,
2026-11-25; 1 MB here), a file counted twice, a clone counted as a write.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from _fs_bytes import device_bytes, summary, written
from _tree_clone import clone_tree
from test_fs_churn import XDIST, _session

darwin = pytest.mark.skipif(sys.platform != "darwin", reason="clonefile and FSEvents are macOS's")
MB = 1_000_000
DD = (
    "import subprocess\n"
    "def test_dd(tmp_path):\n"
    "    subprocess.run(['dd', 'if=/dev/zero', f'of={tmp_path}/f', 'bs=1000000', 'count=1'],\n"
    "                   check=True, capture_output=True, stdin=subprocess.DEVNULL)\n"
    "def test_quiet(tmp_path):\n"
    "    pass\n"
)


@pytest.mark.load_sensitive
@darwin
@pytest.mark.parametrize("xdist", [(), XDIST], ids=["plain", "xdist"])
def test_a_childs_write_is_its_tests_bytes(tmp_path: Path, xdist: tuple[str, ...]) -> None:
    r, rep = _session(tmp_path, DD, {"per_test": 10_000, "per_test_run": 10_000}, *xdist)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    assert rep["bytes_per_test"] == {"test_planted.py::test_dd": MB}, rep
    assert rep["bytes"] == MB and rep["device_bytes"] >= 0, rep
    assert "1.00 MB test_planted.py::test_dd" in r.stdout, r.stdout[-1500:]


def test_a_hard_link_counts_once_and_a_symlink_not_at_all(tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"x" * 5000)
    os.link(tmp_path / "f", tmp_path / "g")
    (tmp_path / "s").symlink_to("f")
    assert written(tmp_path) == (5000, 0)


@darwin
def test_a_clone_of_a_file_outside_wrote_no_bytes_and_one_made_inside_is_counted_once(
    tmp_path: Path,
) -> None:
    mine, outside = tmp_path / "mine", tmp_path / "template"
    for d in (mine, outside):
        d.mkdir()
    (outside / "f").write_bytes(b"x" * 9000)
    clone_tree(outside, mine / "a")
    clone_tree(outside, mine / "b")
    assert written(mine) == (0, 18000)
    with (mine / "a" / "f").open("ab") as f:  # a clone written to is the test's own from then on
        f.write(b"y" * 1000)
        os.fsync(f.fileno())
    assert written(mine) == (10000, 9000)
    (mine / "own").mkdir()
    (mine / "own" / "g").write_bytes(b"z" * 3000)
    clone_tree(mine / "own", mine / "own2")
    assert written(mine) == (13000, 9000)


@darwin
def test_the_device_counter_reads() -> None:
    n = device_bytes()
    assert n is not None and n > 0


def test_the_summary_ranks_the_tests_and_names_the_device() -> None:
    rep = {
        "bytes": 3 * MB, "cloned_bytes": MB, "device_bytes": 50 * MB,
        "bytes_per_test": {f"t{i}": (30 - i) * MB for i in range(30)},
    }  # fmt: skip
    lines = summary(rep)
    assert "3.0 MB left written" in lines[0] and "50 MB" in lines[0] and len(lines) == 21
    assert "t0" in lines[1]
