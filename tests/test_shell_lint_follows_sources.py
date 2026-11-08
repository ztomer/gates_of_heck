"""`goh shell-lint` judges a script together with what it sources (GOH.SHX).

`shellcheck -x tools/release-kit/release.sh` named SC2178 and SC2128 (a variable a sourced library
uses as an array, reused as a string) while the house lint passed it: the lint ran one shellcheck
per file WITHOUT `-x`, so no finding that needs a sourced file could reach any repo. These pin the
class at both scopes, the tree a staged run reads its sources from (the index, never the worktree),
and the verdict cache, whose key must move when a SOURCED file moves.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from conftest import commit_all, git, run_goh, stage, write

pytestmark = pytest.mark.skipif(shutil.which("shellcheck") is None, reason="needs shellcheck")

# A library whose function uses `items` as an array; a script that sources it and reuses the name
# as a string. Clean alone, SC2178 + SC2128 under `shellcheck -x` -- release.sh's shape exactly.
LIB_ARRAY = (
    '#!/usr/bin/env bash\nhelper() {\n  local items=()\n  items+=("$1")\n  echo "${items[@]}"\n}\n'
)
LIB_PLAIN = '#!/usr/bin/env bash\nhelper() {\n  echo "$1"\n}\n'
MAIN = (
    "#!/usr/bin/env bash\n# shellcheck source=lib.sh\n. ./lib.sh\n"
    'join() {\n  local items=""\n  items="$1"\n  echo "$items"\n}\njoin a\nhelper b\n'
)


def _lint(repo: Path, *args: str, cache: Path | None = None):
    env = {k: v for k, v in os.environ.items() if k != "SHELLCHECK_OPTS"}
    if cache is None:
        env["GOH_VERDICT_CACHE"] = "0"
    else:
        env["GOH_VERDICT_DIR"] = str(cache)
    r = run_goh(repo, "shell-lint", *args, env=env)
    return r.returncode, r.stdout + r.stderr


def test_a_finding_that_needs_the_sourced_file_is_red_at_full_scope(repo):
    write(repo, "lib.sh", LIB_ARRAY)
    write(repo, "main.sh", MAIN)
    commit_all(repo)
    rc, out = _lint(repo)
    assert rc == 1, out
    assert "main.sh#SC2178" in out and "main.sh#SC2128" in out, out


def test_the_same_script_is_clean_when_its_library_holds_no_array(repo):
    # The other direction: the finding is the library's array, not something in main.sh alone.
    write(repo, "lib.sh", LIB_PLAIN)
    write(repo, "main.sh", MAIN)
    commit_all(repo)
    rc, out = _lint(repo)
    assert rc == 0, out


def test_a_staged_script_reads_its_library_from_the_index_not_the_worktree(repo):
    write(repo, "lib.sh", LIB_PLAIN)
    commit_all(repo)
    write(repo, "lib.sh", LIB_ARRAY)
    write(repo, "main.sh", MAIN)
    stage(repo, "lib.sh", "main.sh")
    write(repo, "lib.sh", LIB_PLAIN)  # an unstaged edit back: the worktree is clean, the index not
    rc, out = _lint(repo, "--staged")
    assert rc == 1, out
    assert "main.sh#SC2178" in out, out
    # ...and the converse: the array only in the worktree is not what is being committed.
    git(repo, "add", "lib.sh")
    write(repo, "lib.sh", LIB_ARRAY)
    rc, out = _lint(repo, "--staged")
    assert rc == 0, out


def test_a_cached_verdict_is_not_reused_once_a_sourced_file_moves(repo, tmp_path):
    cache = tmp_path / "cache"
    write(repo, "lib.sh", LIB_PLAIN)
    write(repo, "main.sh", MAIN)
    commit_all(repo)
    rc, out = _lint(repo, cache=cache)
    assert rc == 0, out
    write(repo, "lib.sh", LIB_ARRAY)  # main.sh's bytes are unchanged
    commit_all(repo)
    rc, out = _lint(repo, cache=cache)
    assert rc == 1, out
    assert "main.sh#SC2178" in out, out
