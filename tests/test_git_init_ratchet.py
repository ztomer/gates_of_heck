"""No NEW per-test repository or goh-tree copy in a test file (GOH.GITINITGATE, git_init_lint.py).

152 test files ran `git init` (most through `fast_init`), 53 copied goh's tree: each a burst of new
paths for fseventsd (GOH.FSCHURN3). The shared template builds such a tree once per run; this
ratchet holds every file at or below today's count in tests/git_init_seed.json, and a file below its
seed fails until the seed is lowered with it, so the list only shrinks.
"""

import json
from pathlib import Path

import git_init_lint

TESTS = Path(__file__).resolve().parent
SEED = TESTS / "git_init_seed.json"
INIT = "git" + " init"  # spelled apart, so this file is no site itself


def test_every_file_is_at_its_seed() -> None:
    sites = git_init_lint.census(TESTS)
    seed = {**dict.fromkeys(sites, 0), **json.loads(SEED.read_text(encoding="utf-8"))}
    found = {**dict.fromkeys(seed, 0), **{name: len(lines) for name, lines in sites.items()}}
    over = {n: (c, seed[n], sites[n]) for n, c in found.items() if c > seed[n]}
    assert not over, (
        "a new per-test repo or goh-tree copy -- build it once with the shared template "
        "(`template`/`from_template` in tests/_tree_clone.py, a `_build*` builder, or the `repo` "
        f"fixture): {{file: (count, seed, lines)}} = {over}"
    )
    under = {n: (found[n], s) for n, s in seed.items() if found[n] < s}
    assert not under, f"fewer sites than the seed: lower tests/git_init_seed.json to match {under}"


def test_each_spelling_is_a_site() -> None:
    src = (
        "import subprocess, shutil\n"
        "def t(tmp_path):\n"
        "    fast_init(tmp_path)\n"
        "    subprocess.run(['git', '-C', str(tmp_path), 'init', '-q'])\n"
        "    _git(tmp_path, 'init')\n"
        f"    subprocess.run('{INIT} x', shell=True)\n"
        "    shutil.copytree(REPO_ROOT / 'lib', tmp_path / 'lib')\n"
    )
    assert git_init_lint.sites(src) == [3, 4, 5, 6, 7]


def test_a_template_builder_and_a_docstring_are_not_sites() -> None:
    src = (
        f'"""Runs {INIT} once."""\n'
        "def _build_repo(r):\n"
        "    fast_init(r)\n"
        "    subprocess.run(['git', 'init'], cwd=r)\n"
        "def t(tmp_path):\n"
        "    subprocess.run(['git', 'status'])\n"
        "    shutil.copytree(FIXTURES, tmp_path)\n"
    )
    assert git_init_lint.sites(src) == []
