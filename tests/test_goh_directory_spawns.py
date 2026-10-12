"""This repo's own processes run in another directory only with a stated environment (4.2).

The census of `checks/_gitcensus.py` over this tree: every spawn with a `cwd` either takes an
environment that is not the hook's or lives where an inheritance is declared, with its reason.
"""

import os
from pathlib import Path

from _gitcensus import census

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ["checks", "gates", "hooks", "tools", "tui", "tests"]
INHERIT = {
    # conftest.py drops `local_env_vars()` from os.environ before any test runs, so every child
    # a test starts, in any directory, starts with no repository named.
    "tests": "conftest drops git's local env vars at session start",
    "checks/check_swift_warnings.py": "runs git and swift in the repo it judges, the gated one",
}


def test_the_tests_declaration_holds_no_repository_is_named_in_this_process():
    """What the `tests` declaration rests on, read rather than trusted: under a hook GIT_DIR is set,
    and by the time a test runs conftest has dropped it."""
    assert "GIT_DIR" not in os.environ and "GIT_INDEX_FILE" not in os.environ


def test_every_spawn_in_another_directory_states_its_environment():
    got = census(ROOT, SOURCES, inherit=INHERIT)
    assert got.read > 100, f"the census read {got.read} files: aimed at the wrong tree?"
    assert got.fixture_sites == [], "\n".join(
        f"{s.path}:{s.line}: {s.source}" for s in got.fixture_sites
    )
    assert got.stale_inherit == []
