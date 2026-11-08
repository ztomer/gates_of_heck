"""No plan keeps the installed version by hand (`goh installed-version`, GOH.INSTLINE).

A line saying which version is INSTALLED is true the day it is written and false at the next
release: koffee's FEATURE_PLAN said v2.12.1 build 36 while the Mac ran 2.13.0 build 40 (KB.HYG3,
koffee_big ff18e06), and app_updates, sys_updater and ztools each carried a "Shipped and installed:
vX" line. What a machine runs is read from the machine; a release is recorded by its tag. The rule
moved from koffee's own citation gate into the house, so every repo runs one copy of it.

Its subject is a PLAN -- a tracked Markdown file whose name has a ROADMAP, PLAN, BACKLOG or TODO
word. A CHANGELOG or a decision record is dated history: "the installed v0.43.1 bundle" there is
evidence of what was true that day, and it stays true.
"""

from __future__ import annotations

import pytest
from conftest import commit_all, run_goh, stage, write

PLANTED = (
    "**Installed:** v2.12.1 build 36 on both devices",
    "- **Shipped and installed: v1.59.0** (2026-11-07)",
    "the installed build 40 reads",
    "Installed on the Pixel: versionCode 34",
)


@pytest.mark.parametrize("line", PLANTED)
@pytest.mark.parametrize("name", ["ROADMAP.md", "FEATURE_PLAN.md", "docs/BACKLOG.md", "todo.md"])
def test_a_hand_kept_installed_version_in_a_plan_is_red(repo, name, line):
    write(repo, name, f"# Plan\n\n{line}\n")
    commit_all(repo)
    r = run_goh(repo, "installed-version")
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"{name}:3" in r.stderr and "installed version" in r.stderr, r.stderr


def test_it_is_refused_at_commit_too(repo):
    write(repo, "ROADMAP.md", "# Plan\n\n- Shipped and installed: v3.11.1\n")
    stage(repo, "ROADMAP.md")
    r = run_goh(repo, "installed-version", "--staged")
    assert r.returncode == 1, r.stdout + r.stderr


def test_it_is_a_structural_step_every_repo_runs(repo):
    write(repo, "ROADMAP.md", "# Plan\n\n- Shipped and installed: v3.11.1\n")
    commit_all(repo)
    r = run_goh(repo, "structural", "--full", "--keep-going")
    assert r.returncode != 0, r.stdout + r.stderr
    assert "ROADMAP.md:3" in r.stdout + r.stderr


@pytest.mark.parametrize(
    "name, line",
    [
        ("ROADMAP.md", "the installed app reads its own Info.plist"),
        ("ROADMAP.md", "Releases: https://github.com/o/r/releases (the tag is the record)"),
        ("CHANGELOG.md", "- Verified in the installed app (v0.43.1 bundle)"),
        ("docs/decisions/D-0248.md", "the installed v2.77.0 build 135 carried a dylib"),
        ("EXPLANATION.md", "the installed v2.77.0"),
    ],
)
def test_history_and_versionless_lines_pass(repo, name, line):
    write(repo, name, f"# Doc\n\n{line}\n")
    commit_all(repo)
    r = run_goh(repo, "installed-version")
    assert r.returncode == 0, r.stdout + r.stderr
