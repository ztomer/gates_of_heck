"""checks/check_git_env.py -- the house gate around the git census (BACKLOG 6.1).

necrohand's and CadGoose2's hand-written `check_git_env.py` walked the disk for Python, so in
`check_empty_scope`'s skeleton -- the gate directory on disk but ignored -- they found the gates
themselves and passed over a tree with no content. This gate reads git's listing through
`_gitcensus.census` and refuses a census that read nothing. ZeroThunder's copy of the hook-env
scrub was typed by hand and missed `GIT_PREFIX`; the house scrub reads git's own list.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from _gitutil import foreign_repo_env, local_env_vars
from conftest import commit_all, run_check, write

GATE = "checks/check_git_env.py"
RAW = 'import subprocess\n\n\ndef go(root):\n    return subprocess.run(["git", "-C", root, "status"])\n'
ROUTED = (
    'import git_env\n\n\ndef go(root):\n    return git_env.run(["git", "-C", root, "status"])\n'
)


def _config(repo: Path, **sections) -> None:
    write(repo, "tools/git_env.json", json.dumps({"sources": ["tools"], **sections}))


def test_a_raw_git_spawn_is_named_by_file_and_line(repo: Path) -> None:
    _config(repo)
    write(repo, "tools/a.py", RAW)
    commit_all(repo)
    r = run_check(repo, GATE)
    assert r.returncode == 1 and "tools/a.py:5 spawns git directly" in r.stderr, r.stdout + r.stderr


def test_a_call_through_the_seam_passes_and_says_what_it_read(repo: Path) -> None:
    _config(repo)
    write(repo, "tools/a.py", ROUTED)
    commit_all(repo)
    r = run_check(repo, GATE)
    assert r.returncode == 0 and "1 Python file(s) read" in r.stdout, r.stdout + r.stderr


def test_the_empty_scope_skeleton_is_refused_not_passed(repo: Path) -> None:
    """The consumers' defect: the gate directory is on disk but ignored, as `check_empty_scope`
    builds it. A disk walk finds the gates and passes; git's listing finds nothing, and refuses."""
    _config(repo)
    write(repo, "tools/routed.py", ROUTED)
    (repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("/tools/\n")
    r = run_check(repo, GATE)
    assert r.returncode == 1 and "read no Python under tools" in r.stderr, r.stdout + r.stderr


def test_a_declaration_that_matches_nothing_is_refused(repo: Path) -> None:
    _config(
        repo,
        exempt={"tools/a.py": "it spawned git once"},
        inherit={"tools/": "it ran a fixture once"},
        shell={"tools/run.sh": "it ran git once"},
    )
    write(repo, "tools/a.py", ROUTED)
    write(repo, "tools/run.sh", "#!/bin/sh\necho no git here\n")
    commit_all(repo)
    r = run_check(repo, GATE)
    assert r.returncode == 1, r.stdout
    for line in ("exempt `tools/a.py`", "inherit `tools/`", "shell `tools/run.sh`"):
        assert line in r.stderr, r.stderr


def test_a_declared_shell_site_that_runs_git_passes(repo: Path) -> None:
    _config(repo, shell={"tools/run.sh": "rev-parse of the tree the hook fired in"})
    write(repo, "tools/a.py", ROUTED)
    write(repo, "tools/run.sh", "#!/bin/sh\nroot=$(git rev-parse --show-toplevel)\n")
    commit_all(repo)
    r = run_check(repo, GATE)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_declaration_without_a_reason_is_refused(repo: Path) -> None:
    _config(repo, exempt={"tools/a.py": " "})
    write(repo, "tools/a.py", RAW)
    commit_all(repo)
    r = run_check(repo, GATE)
    assert r.returncode == 1 and "exempt `tools/a.py` gives no reason" in r.stderr, r.stderr


def test_no_config_is_refused_by_name(repo: Path) -> None:
    write(repo, "tools/a.py", ROUTED)
    commit_all(repo)
    r = run_check(repo, GATE)
    assert r.returncode == 1 and "no git_env.json" in r.stderr, r.stdout + r.stderr


def test_the_probe_goes_red_on_each_refusal(repo: Path) -> None:
    r = run_check(repo, GATE, "--probe")
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_scrub_drops_every_variable_git_binds_a_repository_with() -> None:
    """ZeroThunder's typed copy missed GIT_PREFIX. The house scrub is git's own list, so it holds
    GIT_PREFIX on today's git and whatever a later git adds."""
    hook = {name: "x" for name in local_env_vars()} | {
        "GIT_PREFIX": "sub/",
        "HOME": os.environ["HOME"],
    }
    left = foreign_repo_env(hook)
    assert "GIT_PREFIX" in local_env_vars()
    assert not [k for k in left if k in local_env_vars() or k == "GIT_PREFIX"], left
    assert left["HOME"] == os.environ["HOME"]
