"""checks/_gitcensus -- the house census of where a git argv may be built (BACKLOG 4.1).

Each test is one of the ways the four hand-written consumer copies were wrong or blind: matching
the argv instead of the callee (the seam flags itself), approving a file instead of a call, a scan
that read nothing reported as clean, and a declaration that outlived its call.
"""

from _gitcensus import census, git_spawns
from conftest import write

SEAM = "tools/git_env.py"


def _lines(sites):
    return [(s.path, s.line) for s in sites]


def test_a_hand_rolled_git_argv_is_named_by_file_and_line(tmp_path):
    write(tmp_path, "tools/a.py", 'import subprocess\n\nsubprocess.run(["git", "status"])\n')
    got = census(tmp_path, ["tools"])
    assert _lines(got.sites) == [("tools/a.py", 3)]
    assert got.read == 1


def test_every_raw_spelling_is_raw(tmp_path):
    src = (
        "import subprocess\nfrom subprocess import run, Popen\n"
        'run(["git", "add", "."])\n'
        'Popen(["git", "log"])\n'
        'subprocess.check_output("git rev-parse HEAD".split())\n'
        'subprocess.call(argv, cwd="x") if False else subprocess.call(["git", "gc"])\n'
    )
    assert [line for line, _ in git_spawns(src)] == [3, 4, 5, 6]


def test_a_call_through_the_seam_is_not_a_finding():
    """The CadGoose2 lie: matching the ARGV flags `git_env.run(["git", ...])`, the very call the
    census exists to approve, so the gate can never go green."""
    assert git_spawns('import git_env\ngit_env.run(["git", "status"])\n') == []


def test_approval_is_per_call_not_per_file(tmp_path):
    """The ZeroThunder gap: a file that uses the seam once is not cleared of a raw call beside it."""
    write(
        tmp_path,
        "tools/b.py",
        'import subprocess, foreign_git\nforeign_git.run_git(["git", "x"])\n'
        'subprocess.run(["git", "y"])\n',
    )
    assert _lines(census(tmp_path, ["tools"]).sites) == [("tools/b.py", 3)]


def test_a_non_git_spawn_and_a_word_containing_git_are_not_findings():
    src = 'import subprocess\nsubprocess.run(["ls", "-la"])\nsubprocess.run(["digit", "gitk"])\n'
    assert git_spawns(src) == []


def test_a_declared_file_is_skipped_and_one_that_stopped_spawning_is_stale(tmp_path):
    write(tmp_path, SEAM, 'import subprocess\nsubprocess.run(["git", "status"])\n')
    write(tmp_path, "tools/quiet.py", "x = 1\n")
    got = census(
        tmp_path,
        ["tools"],
        exempt={SEAM: "the seam itself", "tools/quiet.py": "used to read HEAD"},
    )
    assert got.sites == []
    assert got.stale == ["tools/quiet.py"]


def test_a_census_aimed_at_nothing_says_it_read_nothing(tmp_path):
    got = census(tmp_path, ["no-such-dir"])
    assert (got.read, got.sites) == (0, [])


def test_a_file_it_cannot_parse_is_reported_never_read_as_clean(tmp_path):
    write(tmp_path, "tools/broken.py", 'subprocess.run(["git", "x"]\n')
    got = census(tmp_path, ["tools"])
    assert got.read == 0
    assert [rel for rel, _ in got.unreadable] == ["tools/broken.py"]


def test_build_output_and_vendored_trees_are_pruned(tmp_path):
    write(tmp_path, "tools/.venv/lib/x.py", 'import subprocess\nsubprocess.run(["git", "x"])\n')
    write(tmp_path, "tools/__pycache__/y.py", 'import subprocess\nsubprocess.run(["git", "y"])\n')
    assert census(tmp_path, ["tools"]).sites == []


def test_in_a_work_tree_what_git_ignores_is_not_read(repo):
    """The listing is git's: generated code under an ignored directory is not the repo's."""
    write(repo, ".gitignore", "gen/\n")
    write(repo, "tools/gen/x.py", 'import subprocess\nsubprocess.run(["git", "x"])\n')
    write(repo, "tools/y.py", 'import subprocess\nsubprocess.run(["git", "y"])\n')
    got = census(repo, ["tools"])
    assert _lines(got.sites) == [("tools/y.py", 2)]
    assert got.read == 1
