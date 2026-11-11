"""goh ship's --summary is the release stanza's title (GOH.SHIPSUMMARY).

v0.96.0's heading ended mid-clause ("... and v0.95.0's bump was made by"): `goh ship --summary`
reached the preflight but never the release kit, so tools/release_goh.sh saw no GOH_SHIP_SUMMARY
and the cut derived a title from the first entry, clipped at 90 characters.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import git
from test_ship import _changelog, _ship, make_shipped


@pytest.fixture
def shipped(repo: Path, tmp_path: Path) -> Path:
    return make_shipped(repo, tmp_path)


# The exact first entry of goh v0.96.0, after its bold lead.
CLIPPED_096 = (
    "# CHANGELOG\n\n## Unreleased\n\n"
    "* goh declared no GOH_SHIP_RELEASE, so `goh ship` refused it and v0.95.0's bump was made by\n"
    "  hand. The kit refuses a dirty tree.\n\n## v1.0.0 -- the first\n\n* the first\n"
)


@pytest.mark.parametrize("item", [(), ("--item", "X.ONE")])
def test_the_release_kit_sees_the_summary_and_the_item(shipped, tmp_path, item):
    seen = tmp_path / "seen"
    kit = (f'printf "%s|%s" "$GOH_SHIP_SUMMARY" "$GOH_SHIP_ITEM" > {seen} && '
           'git tag "v$GOH_SHIP_VERSION" && git push -q origin "v$GOH_SHIP_VERSION"')  # fmt: skip
    r = _ship(shipped, tmp_path, "--wait", "--summary", "what the user gets", *item,
              GOH_SHIP_RELEASE=kit)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert seen.read_text() == f"what the user gets|{item[1] if item else ''}"


def test_a_clipped_derived_title_is_refused_before_the_push(shipped, tmp_path):
    _changelog(shipped, CLIPPED_096)
    before = git(shipped, "ls-remote", "origin")
    r = _ship(shipped, tmp_path, "--wait")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "past 90" in r.stderr and "--summary" in r.stderr, r.stderr
    assert git(shipped, "ls-remote", "origin") == before


def test_a_summary_titles_a_stanza_the_entry_could_not(shipped, tmp_path):
    _changelog(shipped, CLIPPED_096)
    r = _ship(shipped, tmp_path, "--wait", "--summary", "goh ships itself")
    assert r.returncode == 0, r.stdout + r.stderr
