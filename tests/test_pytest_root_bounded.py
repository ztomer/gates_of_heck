"""A kept pytest temp root is bounded: by what pytest keeps inside it, and by how many and how much
of them survive (GOH.PYTESTTMP).

TMPDIR held 55.3 GiB on 2026-11-12: eleven kept `goh-pytest.*` roots of ~5 GiB each, every test's
tmp_path of every failed run, held 12 h by the backstop. So (1) the root claims its owner's pid, and
a live root is never cut however old its top directory looks; (2) older kept roots are cut to the
newest N AND a byte budget; (3) pytest keeps only a FAILED test's tmp_path.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "lib"))
from prune_kept import prune

MIB = 1 << 20
OLD = time.time() - 7200


def _root(parent: Path, name: str, mib: int, age: float) -> Path:
    root = parent / name
    deep = root / "pytest-of-u" / "pytest-0" / "popen-gw0"
    deep.mkdir(parents=True)
    (deep / "blob").write_bytes(b"\0" * (mib * MIB))
    for d in (deep, deep.parent, deep.parent.parent, root):
        os.utime(d, (age, age))
    return root


def test_kept_roots_are_bounded_by_size_as_well_as_count(tmp_path) -> None:
    for i in range(5):
        _root(tmp_path, f"goh-pytest.{i}", 1, OLD + i)  # .4 is the newest
    removed = prune(str(tmp_path), "goh-pytest.", keep=3, idle=3600, max_bytes=int(2.5 * MIB))
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["goh-pytest.3", "goh-pytest.4"], left  # the count allowed 3; the size, 2
    assert sorted(os.path.basename(r) for r in removed) == [f"goh-pytest.{i}" for i in range(3)]


def test_a_root_whose_owner_is_alive_is_never_cut(tmp_path) -> None:
    root = _root(tmp_path, "goh-pytest.live", 1, OLD)
    (root / ".goh-owner").write_text(f"{os.getpid()}\n")
    os.utime(root, (OLD, OLD))
    assert prune(str(tmp_path), "goh-pytest.", keep=0, idle=3600, max_bytes=0) == []
    assert root.exists()


def test_a_root_busy_deep_in_its_tree_is_live(tmp_path) -> None:
    """A suite writes `popen-gwK/<test>` dirs as it goes; its root's own mtime stays old."""
    root = _root(tmp_path, "goh-pytest.busy", 1, OLD)
    now = time.time()
    gw = root / "pytest-of-u" / "pytest-0" / "popen-gw0"
    os.utime(gw, (now, now))
    assert prune(str(tmp_path), "goh-pytest.", keep=0, idle=3600) == []
    assert root.exists()


def test_a_dead_owner_does_not_keep_a_root(tmp_path) -> None:
    root = _root(tmp_path, "goh-pytest.dead", 1, OLD)
    (root / ".goh-owner").write_text("999999999\n")  # above any pid macOS or Linux hands out
    os.utime(root, (OLD, OLD))
    assert prune(str(tmp_path), "goh-pytest.", keep=0, idle=3600) == [str(root)]


PASSES_AND_FAILS = """\
def test_passes(tmp_path):
    (tmp_path / "big").write_bytes(b"x" * 4096)


def test_fails(tmp_path):
    (tmp_path / "evidence").write_text("kept")
    assert False
"""


def test_the_root_keeps_only_a_failed_tests_tmp_path_and_cuts_old_roots(tmp_path) -> None:
    tmpdir = tmp_path / "T"
    tmpdir.mkdir()
    for i in range(4):
        _root(tmpdir, f"goh-pytest.old{i}", 1, OLD + i)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "test_mixed.py").write_text(PASSES_AND_FAILS)
    script = (
        '. "$1/gates/_pytest_root.sh" && goh_pytest_root && '
        'test "$(cat "$PYTEST_DEBUG_TEMPROOT/.goh-owner")" = "$$" && '
        '{ "$2" -m pytest -q -p no:cacheprovider -p no:xdist test_mixed.py >/dev/null; '
        'printf "%s\\n" "$PYTEST_DEBUG_TEMPROOT"; }'
    )
    r = subprocess.run(
        ["/bin/bash", "-c", script, "_", str(REPO_ROOT), sys.executable],
        cwd=proj,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=hermetic_env(TMPDIR=str(tmpdir), GOH_PY=sys.executable, GOH_PYTEST_KEEP="2"),
        check=False,
    )
    lines = r.stdout.split()
    assert r.returncode == 0 and lines, r.stdout + r.stderr
    root = Path(lines[-1])
    names = sorted(p.name for p in tmpdir.iterdir())
    assert names == sorted(["goh-pytest.old2", "goh-pytest.old3", root.name]), names
    kept = sorted(p.parent.name for p in root.rglob("evidence"))
    assert kept == ["test_fails0"], kept
    assert not list(root.rglob("big")), "a passed test's tmp_path outlived its test"
