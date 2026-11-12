"""A sibling repo reaches a consumer's gate at its HEAD commit, never its working tree.

GOH.HEADEXPORT.

ZeroThunder's pre-push gate imported game_asset_factory from the live checkout, and a concurrent
agent's UNCOMMITTED edit there (NameError 'counted') failed a push for code in neither repo's
history (2026-11-11). ZeroThunder fixed it with a repo-local export; three more consumers carried
the same working-tree path. `lib/head_export.sh` (`goh_head_export VAR SRC [CACHE]`) is the one
public helper: a read-only, sha-keyed CLONE of SRC's HEAD (a clone, not `git archive`: the factory
finds its own root through git), with a ceiling whose eviction makes the tree writable first.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, commit_all, git, hermetic_env, write

sys.path.insert(0, str(REPO_ROOT / "lib"))
import head_export

PROBE = "import mod; print(mod.VALUE)"


@pytest.fixture
def sibling(repo: Path) -> Path:
    """`mod.VALUE = 1` committed, then a peer's uncommitted edit and an untracked file."""
    write(repo, "mod.py", "VALUE = 1\n")
    write(repo, "pkg/__init__.py", "")
    commit_all(repo)
    write(repo, "mod.py", "raise NameError(\"name 'counted' is not defined\")  # uncommitted\n")
    write(repo, "untracked.py", "X = 1\n")
    return repo


def _export(src: Path, cache: Path, *extra: str, **env: str) -> subprocess.CompletedProcess:
    script = (
        '. "$1/lib/head_export.sh" && goh_head_export OUT "$2" "$3" "${4:-}"'
        ' && printf "%s\\n" "$OUT"'
    )
    return subprocess.run(
        ["/bin/bash", "-c", script, "_", str(REPO_ROOT), str(src), str(cache), *extra],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=hermetic_env(drop_git=True, **env),
        check=False,
    )


def _writable(root: Path) -> None:
    for dirpath, _d, _f in os.walk(root):
        os.chmod(dirpath, 0o755)


@pytest.fixture
def cache(tmp_path: Path):
    c = tmp_path / "cache"
    yield c
    if c.exists():
        _writable(c)


def test_a_dirty_sibling_working_tree_never_reaches_the_consumer(sibling, cache):
    r = _export(sibling, cache)
    assert r.returncode == 0, r.stderr
    dest = Path(r.stdout.strip())
    sha = git(sibling, "rev-parse", "HEAD").strip()
    assert dest == cache / sha
    run = subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(dest)!r}); {PROBE}"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert run.returncode == 0 and run.stdout.strip() == "1", run.stderr
    assert not (dest / "untracked.py").exists()
    assert (dest / head_export.MARKER).read_text().splitlines() == [sha, str(sibling.resolve())]


def test_the_export_is_a_clean_checkout_git_can_see(sibling, cache):
    dest = Path(_export(sibling, cache).stdout.strip())
    top = git(dest, "rev-parse", "--show-toplevel").strip()
    assert Path(top).resolve() == dest.resolve()
    assert git(dest, "status", "--porcelain") == ""
    assert git(dest, "rev-parse", "HEAD").strip() == dest.name


def test_the_export_is_read_only(sibling, cache):
    dest = Path(_export(sibling, cache).stdout.strip())
    assert not os.stat(dest / "mod.py").st_mode & 0o222
    for attempt in (
        lambda: (dest / "mod.py").write_text("x"),
        lambda: (dest / "pkg" / "new.py").write_text("x"),
    ):
        with pytest.raises(PermissionError):
            attempt()


def test_an_export_is_answered_as_is_and_a_hook_git_dir_is_ignored(sibling, cache, tmp_path):
    dest = _export(sibling, cache).stdout.strip()
    nested = _export(Path(dest), cache)
    assert nested.returncode == 0 and nested.stdout.strip() == dest, nested.stderr
    other = tmp_path / "other"
    fast_init(other)
    bound = _export(sibling, cache, GIT_DIR=str(other / ".git"))
    assert bound.returncode == 0 and bound.stdout.strip() == dest, bound.stderr


def test_a_source_with_no_head_is_refused_by_name(tmp_path, cache):
    plain = tmp_path / "plain"
    plain.mkdir()
    r = _export(plain, cache)
    assert r.returncode == 2
    assert str(plain) in r.stderr and "HEAD" in r.stderr


def test_a_stale_non_export_at_the_sha_is_replaced(sibling, cache):
    sha = git(sibling, "rev-parse", "HEAD").strip()
    stale = cache / sha
    stale.mkdir(parents=True)
    (stale / "junk").write_text("x")
    r = _export(sibling, cache)
    assert r.returncode == 0, r.stderr
    assert head_export.is_export(str(stale)) and not (stale / "junk").exists()


def _old_export(cache: Path, name: str, used_ago: float) -> Path:
    d = cache / name
    (d / "deep").mkdir(parents=True)
    (d / "deep" / "f").write_text("x")
    (d / head_export.MARKER).write_text(name + "\n")
    (d / head_export.USED).write_text("")
    then = time.time() - used_ago
    os.utime(d / head_export.USED, (then, then))
    (d / "deep").chmod(0o555)
    return d


def test_the_ceiling_evicts_read_only_exports_whole_and_never_one_in_use(sibling, cache):
    cache.mkdir()
    old = [_old_export(cache, f"old{i}", 7200 + i) for i in range(4)]
    fresh = _old_export(cache, "fresh", 60)
    r = _export(sibling, cache, "2")
    assert r.returncode == 0, r.stderr
    sha = git(sibling, "rev-parse", "HEAD").strip()
    left = {p.name for p in cache.iterdir()}
    # keep=2: the current one stays, and the one in use is never evicted
    assert left == {sha, "fresh"}, left
    assert fresh.exists() and not any(p.exists() for p in old)


def test_removal_never_touches_a_hardlinked_inode_outside_the_tree(tmp_path):
    """A clone's objects are HARD LINKS to the source's: making the tree writable must not chmod
    them, or every eviction rewrites the source repository's object modes."""
    outside = tmp_path / "source-object"
    outside.write_text("x")
    outside.chmod(0o444)
    tree = tmp_path / "tree"
    (tree / "d").mkdir(parents=True)
    os.link(outside, tree / "d" / "obj")
    (tree / "d").chmod(0o555)
    head_export.remove(str(tree))
    assert not tree.exists()
    assert stat.S_IMODE(outside.stat().st_mode) == 0o444
