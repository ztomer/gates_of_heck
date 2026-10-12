"""checks/_gitcensus -- a process run in another directory states the environment it takes (4.2).

A child spawned with `cwd=<fixture>` that inherits the hook's environment inherits the hook's
REPOSITORY: GIT_DIR and GIT_INDEX_FILE name the real one, so a house check run inside a fixture
lists the real tree and a repository made there re-initialises the real one. No git-argv census sees it, because
the argv names a checker. Both known instances are here by shape: game_asset_factory's
`run_check` and ZeroThunder's `run_in`/`emoji_rejected`.
"""

from _gitcensus import census, directory_spawns
from conftest import write

GAF_RUN_CHECK = """\
import subprocess, sys
from gaf.core.subproc import foreign_repo_env

def run_check(repo, script, argv):
    return subprocess.run(
        [sys.executable, script, *argv],
        stdin=subprocess.DEVNULL,
        cwd=repo,
        capture_output=True,
        env=foreign_repo_env(),
        check=False,
    ).returncode
"""

ZT_EMOJI_REJECTED = """\
import os, subprocess

def gate(repo):
    def emoji_rejected(env_extra):
        env = dict(git_env_for(repo))
        env.update(env_extra)
        return subprocess.run(["bash", "tools/gate.sh", "--staged"], cwd=repo, env=env, check=False)
    return emoji_rejected
"""


def _lines(sites):
    return [(s.path, s.line) for s in sites]


def test_the_scrubbed_run_check_is_clean_and_without_its_scrub_it_is_named(tmp_path):
    """game_asset_factory's `run_check`: with `foreign_repo_env()` it passes; without it the
    census names the spawn, the fixture's cwd and all."""
    write(tmp_path, "tools/check_gate_canary.py", GAF_RUN_CHECK)
    assert census(tmp_path, ["tools"]).fixture_sites == []
    write(
        tmp_path,
        "tools/check_gate_canary.py",
        GAF_RUN_CHECK.replace("        env=foreign_repo_env(),\n", ""),
    )
    got = census(tmp_path, ["tools"]).fixture_sites
    assert _lines(got) == [("tools/check_gate_canary.py", 5)]
    assert "cwd=repo" in got[0].source


def test_an_environment_copied_from_os_environ_is_the_hooks_and_is_named():
    """ZeroThunder's `emoji_rejected` as it first was: `dict(os.environ, **extra)` carries GIT_DIR."""
    assert directory_spawns(ZT_EMOJI_REJECTED) == []
    for inherited in (
        "os.environ",
        "dict(os.environ, **env_extra)",
        "{**os.environ, 'X': '1'}",
        "os.environ.copy()",
    ):
        src = ZT_EMOJI_REJECTED.replace("env=env,", f"env={inherited},")
        assert [line for line, _ in directory_spawns(src)] == [7], inherited


def test_a_spawn_in_its_own_directory_is_not_a_finding():
    src = 'import subprocess\nsubprocess.run(["swift", "build"])\nsubprocess.run(["git", "status"], env=None)\n'
    assert directory_spawns(src) == []


def test_a_declared_directory_or_file_may_inherit_and_a_declaration_with_nothing_under_it_is_stale(
    tmp_path,
):
    spawn = 'import subprocess\nsubprocess.run(["swift", "build"], cwd=root)\n'
    write(tmp_path, "tests/test_a.py", spawn)
    write(tmp_path, "checks/judge.py", spawn)
    write(tmp_path, "checks/quiet.py", "x = 1\n")
    got = census(
        tmp_path,
        ["tests", "checks"],
        inherit={
            "tests": "conftest drops git's local env vars at session start",
            "checks/judge.py": "runs in the repo it judges, which is the gated one",
            "checks/quiet.py": "used to spawn in the root",
        },
    )
    assert got.fixture_sites == []
    assert got.stale_inherit == ["checks/quiet.py"]
