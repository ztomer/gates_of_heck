"""A `.gatesrc` key assigned on two lines is refused (GOH.GATESRCDUPES).

Bash and goh both take the LAST assignment, so the earlier line is dead text and the later one
silently wins: `scripts` set its scanner floors twice and the lower block won (SC.PRUNECONFIG,
2026-11-12). End to end through `goh structural --full`: red naming the key and both lines, a key
set once is green, the repo's own seed warns, and a seed for a key that is no longer repeated is a
stale entry (red)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from conftest import commit_all, git, run_goh, write

LABEL = "every .gatesrc key is set once"


def _full(repo: Path) -> tuple[int, str]:
    # The floors are not this file's subject: the suite's seam keeps an undeclared one advisory.
    env = {**os.environ, "GOH_FLOOR_UNDECLARED": "allow", "GOH_SEED_DIR": str(repo / "no-store")}
    r = run_goh(repo, "structural", "--full", env=env)
    return r.returncode, r.stdout + r.stderr


def test_a_key_set_twice_is_red_naming_the_key_and_both_lines(repo: Path) -> None:
    write(repo, ".gatesrc", "GOH_MAX_LINES=500\nGOH_EMOJI_MIN_FILES=10\nGOH_EMOJI_MIN_FILES=1\n")
    commit_all(repo)
    code, out = _full(repo)
    assert code == 1, out
    assert "GOH_EMOJI_MIN_FILES is set 2 times (lines 2, 3)" in out, out
    assert LABEL in out, out


def test_a_key_set_once_is_green_and_a_comment_is_not_an_assignment(repo: Path) -> None:
    write(repo, ".gatesrc", "GOH_MAX_LINES=500\n# GOH_MAX_LINES=300\nGOH_EMOJI_MIN_FILES=1\n")
    commit_all(repo)
    code, out = _full(repo)
    assert code == 0, out
    assert LABEL in out, out


def test_the_repos_own_seed_warns_and_a_fixed_file_makes_it_stale(repo: Path) -> None:
    root = git(repo, "rev-list", "--max-parents=0", "HEAD").split()[0]
    write(repo, ".gatesrc", "GOH_MAX_LINES=500\nGOH_MAX_LINES=400\n")
    seed = {"entries": [{"root": root, "repo": "fixture", "path": ".gatesrc", "count": 1,
                         "reason": "unreviewed: set it once"}]}  # fmt: skip
    write(repo, "gatesrc_dupes_seed.json", json.dumps(seed))
    commit_all(repo)
    code, out = _full(repo)
    assert code == 0, out
    assert "seeded" in out and "GOH_MAX_LINES" in out, out
    write(repo, ".gatesrc", "GOH_MAX_LINES=500\n")
    commit_all(repo)
    code, out = _full(repo)
    assert code == 1 and "stale" in out, out
