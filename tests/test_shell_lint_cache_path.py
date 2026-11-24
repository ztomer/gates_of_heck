"""A shell-lint verdict answers for the same bytes wherever the checkout sits (GOH.TAGPUSHGATE).

The cache key named the repo's own `.shellcheckrc` by its ABSOLUTE path, so every checkout was a
cache of its own: v0.125.0's reduced push re-ran shellcheck over 104 unchanged files (36.4 s) that
the release land had judged a minute before in the release worktree, and every lane worktree's
land paid the same again.
"""

from __future__ import annotations

import shutil

import pytest
from _tree_lint import records
from conftest import write
from test_check_shell_lint import _commit_all, run_lint
from tier_kit import both_tiers  # noqa: F401  # both_tiers: a fixture

pytestmark = pytest.mark.usefixtures("both_tiers")


def _records(cache) -> set[str]:
    return records(cache / "goh-cache.db")


def test_a_verdict_answers_for_the_same_bytes_in_another_checkout(repo, tmp_path):
    """The same bytes at another path are answered from the records the first checkout wrote."""
    cache = tmp_path / "cache"
    env = {"GOH_CACHE_DB": str(cache / "goh-cache.db")}  # run_lint lays it over the environment
    write(repo, ".shellcheckrc", "disable=SC1091\n")
    write(repo, "ok.sh", "#!/usr/bin/env bash\necho hi\n")
    write(repo, "lib/two.sh", "#!/usr/bin/env bash\necho two\n")
    _commit_all(repo)
    other = tmp_path / "elsewhere" / "proj"
    shutil.copytree(repo, other, symlinks=True)
    first = run_lint(repo, env=env)
    assert first.returncode == 0, first.stdout + first.stderr
    written = _records(cache)
    assert written, "the first run recorded nothing: the cache is off, so this proves nothing"
    second = run_lint(other, env=env)
    assert second.returncode == 0, second.stdout + second.stderr
    assert _records(cache) == written, "the same bytes at another path missed the cache"


def test_an_rc_edit_in_the_tree_still_misses(repo, tmp_path):
    """The other direction: keyed by its path in the tree, the rc's CONTENT still keys it."""
    cache = tmp_path / "cache"
    env = {"GOH_CACHE_DB": str(cache / "goh-cache.db")}  # run_lint lays it over the environment
    write(repo, ".shellcheckrc", "disable=SC1091\n")
    write(repo, "ok.sh", "#!/usr/bin/env bash\necho hi\n")
    _commit_all(repo)
    assert run_lint(repo, env=env).returncode == 0
    written = _records(cache)
    write(repo, ".shellcheckrc", "disable=SC2034\n")
    _commit_all(repo)
    assert run_lint(repo, env=env).returncode == 0
    assert _records(cache) - written, "an edited rc reused the old verdict"
