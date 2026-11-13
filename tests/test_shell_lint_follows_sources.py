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


# The house idiom: a source named relative to the script, which shellcheck resolves against the CWD
# unless told `--source-path=SCRIPTDIR`.
MAIN_DIRNAME = MAIN.replace(
    "# shellcheck source=lib.sh\n. ./lib.sh", '. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"'
)
# The same, through a library whose name is not `*.sh` (a `.conf`): it is in no shell checkout by
# its extension, so the staged scope must collect it as a source named relative to the script.
MAIN_CONF = MAIN.replace("# shellcheck source=lib.sh\n. ./lib.sh", '. "$(dirname "$0")/lib.conf"')


@pytest.mark.parametrize("scope", ["--full", "--staged"])
@pytest.mark.parametrize(
    ("main", "lib"), [(MAIN_DIRNAME, "lib.sh"), (MAIN_CONF, "lib.conf")], ids=["sh", "conf"]
)
def test_a_source_named_relative_to_the_script_is_followed_in_every_repo(repo, scope, main, lib):
    """Every repo, no rc (GOH.REL2): the lint passes `--source-path=SCRIPTDIR`, so a library a
    script names relative to itself is followed -- measured over the estate first: 0 new findings
    in 37 consumers, and false SC2034s gone in koffee_big and media_server."""
    write(repo, f"tools/{lib}", LIB_ARRAY)
    write(repo, "tools/main.sh", main)
    if scope == "--full":
        commit_all(repo)
        rc, out = _lint(repo)
    else:
        stage(repo, f"tools/{lib}", "tools/main.sh")
        rc, out = _lint(repo, "--staged")
    assert rc == 1, out
    assert "tools/main.sh#SC2178" in out, out


@pytest.mark.parametrize("scope", ["--full", "--staged"])
def test_a_tracked_shellcheckrc_is_read_at_both_scopes(repo, scope):
    """The staged checkout held the shell files and what they name, not the repo's `.shellcheckrc`:
    a rule goh's own rc set held at `--full` and vanished at commit, where gates/python.sh's
    `GOH_NAME` (used by the library it sources) read as SC2034 unused again (GOH.VENDOR)."""
    write(repo, "tools/lib.sh", LIB_ARRAY)
    write(repo, "tools/main.sh", MAIN_DIRNAME)
    commit_all(repo)
    rc, out = _lint(repo)
    assert rc == 1 and "tools/main.sh#SC2178" in out, out
    write(repo, ".shellcheckrc", "disable=SC2178,SC2128\n")
    if scope == "--full":
        commit_all(repo)
        rc, out = _lint(repo)
    else:
        write(repo, "tools/main.sh", MAIN_DIRNAME + "# touched\n")
        stage(repo, ".shellcheckrc", "tools/main.sh")
        rc, out = _lint(repo, "--staged")
    assert rc == 0, "the rc's rule is read: " + out


def test_shellcheck_follows_every_source_in_this_tree(tmp_path):
    """`-x` follows only a source it can find. This tree sources its libraries the house way,
    `. "$(dirname "${BASH_SOURCE[0]}")/../gates/_from_head.sh"`, which shellcheck resolves against
    the CWD unless told otherwise -- so release.sh, land.sh and 30-odd more were linted without the
    file they source (SC1091, an info no severity judges). `.shellcheckrc`'s
    `source-path=SCRIPTDIR` resolves it against the script's own directory; this pins that every
    tracked shell file here has its sources followed (GOH.VENDOR). SC1091 is a parse finding, so
    the files are only parsed, in parallel (tests/_parse_findings.py): 181.7 s on the hosted
    runner when it ran the full analysis on one core (GOH.COVDEDUPE)."""
    import subprocess

    from _parse_findings import findings, probe
    from conftest import REPO_ROOT

    assert any("SC1091" in line for line in probe(tmp_path, "--include=SC1091")), (
        "the instrument cannot see a missing source"
    )
    listed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "*.sh"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.split()
    assert len(listed) > 50, listed
    found = findings(listed, REPO_ROOT, "--include=SC1091")
    assert not [line for line in found if "SC1091" in line], "\n".join(found)
