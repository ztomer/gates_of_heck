"""goh remote-shard's default junit lands under goh's temp root, never in the checkout it runs
from (GOH.REMOTEGATEPATH: a hand run left an untracked .goh-remote-shard.xml in goh's main tree)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
import remote_shard as rs


def test_the_default_junit_is_under_the_temp_root_not_the_tree(tmp_path, monkeypatch):
    tree, tmp = tmp_path / "tree", tmp_path / "tmp"
    tree.mkdir()
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    seen: list[Path] = []

    def fallback(*_args, **_kw):
        raise rs.Fallback("no worker in a test")

    def local(_tree, _tests, _workers, junit):
        seen.append(junit)
        return 0

    monkeypatch.setattr(rs, "probe", fallback)
    monkeypatch.setattr(rs, "run_local", local)
    assert rs.main(["tests/test_x.py", "--tree", str(tree)]) == 0
    assert seen, "the fallback never ran"
    assert not seen[0].resolve().is_relative_to(tree.resolve()), seen[0]
    assert seen[0].resolve().is_relative_to(tmp.resolve()), seen[0]
