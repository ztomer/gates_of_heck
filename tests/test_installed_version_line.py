"""No plan keeps the installed version by hand (`goh installed-version`, GOH.INSTLINE).

A line saying which version is INSTALLED is true the day it is written and false at the next
release: koffee's FEATURE_PLAN said v2.12.1 build 36 while the Mac ran 2.13.0 build 40 (KB.HYG3,
koffee_big ff18e06), and app_updates, sys_updater and ztools each carried a "Shipped and installed:
vX" line. What a machine runs is read from the machine; a release is recorded by its tag. The rule
moved from koffee's own citation gate into the house, so every repo runs one copy of it.

Its subject is every tracked Markdown file but DATED HISTORY (GOH.VENDOR, 2026-11-08: it read only
plans until then, while koffee's own copy read every `.md` -- a README or AGENTS.md that says what is
installed rots the same way). A CHANGELOG or a decision record is history: "the installed v0.43.1
bundle" there is evidence of what was true that day, and it stays true. Measured over the 44-repo
estate that day: every hit outside a plan was in a CHANGELOG or a `docs/decisions/D-*` record.
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
@pytest.mark.parametrize(
    "name",
    [
        "ROADMAP.md",
        "FEATURE_PLAN.md",
        "docs/BACKLOG.md",
        "todo.md",
        "README.md",
        "AGENTS.md",
        "docs/EXPLANATION.md",
    ],
)
def test_a_hand_kept_installed_version_in_a_doc_is_red(repo, name, line):
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
        ("docs/D-0247-the-focus-border.md", "Live, the installed v2.77.0: vmmap listed one"),
        ("docs/adr/0003-signing.md", "the installed v1.2 bundle was ad-hoc signed"),
        ("HISTORY.md", "- the installed v0.9 bundle"),
        ("DECISIONS.md", "#9: the installed v2.1 build 3 floated"),
        ("release-notes/v1.2.md", "the installed v1.1 is replaced"),
        ("README.md", "the installed app reads its own Info.plist"),
    ],
)
def test_history_and_versionless_lines_pass(repo, name, line):
    write(repo, name, f"# Doc\n\n{line}\n")
    commit_all(repo)
    r = run_goh(repo, "installed-version")
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_subject_has_a_floor(repo):
    """A scan that stopped reading Markdown reads as clean: its count is floored like every scanner's."""
    write(repo, "README.md", "# Doc\n")
    write(repo, ".gatesrc", "GOH_INSTALLED_VERSION_MIN_FILES=5\n")
    commit_all(repo)
    r = run_goh(repo, "structural", "--full", "--keep-going")
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "GOH_INSTALLED_VERSION_MIN_FILES" in out and "declares a floor of 5" in out, out
