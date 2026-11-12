"""lib/prune_kept.py: what a tool keeps on failure is bounded -- the newest N, anything recent, and
nothing outside its own prefix."""

import os
import subprocess
import sys
import time

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
from prune_kept import prune


def test_the_newest_and_the_recent_are_kept_and_nothing_else_is_touched(tmp_path) -> None:
    old = time.time() - 7200
    for i in range(25):
        d = tmp_path / f"goh-local-ci.{i:02d}"
        d.mkdir()
        os.utime(d, (old + i, old + i))
    (tmp_path / "goh-local-ci.live").mkdir()  # touched now: a run in flight
    (tmp_path / "other.00").mkdir()
    os.utime(tmp_path / "other.00", (old, old))
    removed = prune(str(tmp_path), "goh-local-ci.", keep=20, idle=3600)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert "goh-local-ci.live" in left and "other.00" in left
    # The newest 20 are the live one and the 19 newest old ones; the 6 oldest go.
    assert len([n for n in left if n.startswith("goh-local-ci.")]) == 20
    assert sorted(os.path.basename(r) for r in removed) == [
        f"goh-local-ci.{i:02d}" for i in range(6)
    ]


def test_a_kept_root_with_a_read_only_tree_is_reaped_not_leaked(tmp_path) -> None:
    """A failed run keeps its `goh-pytest.*` root; a consumer test's read-only tree inside it made
    `rmtree(ignore_errors=True)` remove nothing, silently, for ever (2026-11-12)."""
    root = tmp_path / "goh-pytest.abc123"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "f").write_text("x")
    old = time.time() - 40 * 3600
    for d in (root / "a" / "b", root / "a", root):  # a killed run's tree is old all through
        os.utime(d, (old, old))
    (root / "a" / "b").chmod(0o555)
    (root / "a").chmod(0o555)
    try:
        removed = prune(str(tmp_path), "goh-", keep=0, idle=3600)
        assert [os.path.basename(r) for r in removed] == ["goh-pytest.abc123"]
        assert not root.exists()
    finally:
        if root.exists():
            subprocess.run(
                ["chmod", "-R", "u+rwx", str(root)], stdin=subprocess.DEVNULL, check=False
            )
