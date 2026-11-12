"""goh ship asks CHANGELOG.md under the contract its kit declares, at start (GOH.SHIPCHANGELOG).

token-pool's ship 20261112T024215Z-281911da ran the whole gate and pushed, then its kit said
"CHANGELOG.md has no '## Unreleased' section to release": goh's reading assumed changelog_cut's
contract, under which a file with no Unreleased heading "does not cut from one" and passed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import git
from test_ship import _calls, _changelog, _ship, make_shipped


@pytest.fixture
def shipped(repo: Path, tmp_path: Path) -> Path:
    return make_shipped(repo, tmp_path)


NO_UNRELEASED = "# Changelog\n\n## v1.0.0 (2026-11-11)\n\n- the first\n"


@pytest.mark.parametrize("contract", ["unreleased", "cut"])
def test_a_changelog_the_declared_kit_will_refuse_is_refused_before_any_step(
    shipped, tmp_path, contract
):
    _changelog(shipped, NO_UNRELEASED)
    before = git(shipped, "ls-remote", "origin")
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE", GOH_SHIP_CHANGELOG=contract,
              GOH_SHIP_PREFLIGHT="echo ran > " + str(tmp_path / "pf"))  # fmt: skip
    assert r.returncode == 2, r.stdout + r.stderr
    assert "Unreleased" in r.stderr and "v1.1.0" in r.stderr, r.stderr
    assert git(shipped, "ls-remote", "origin") == before
    assert not (tmp_path / "ship.json").exists() and not (tmp_path / "pf").exists()
    assert _calls(tmp_path) == []


def test_a_kit_that_cuts_its_own_changelog_is_not_refused(shipped, tmp_path):
    _changelog(shipped, NO_UNRELEASED)
    r = _ship(shipped, tmp_path, "--wait", GOH_SHIP_CHANGELOG="kit")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize(
    "text",
    [
        "# C\n\n## Unreleased\n\n- an entry\n\n## v1.0.0\n\n- x\n",
        "# C\n\n## v1.1.0 (2026-11-11)\n\n- by hand\n",
    ],
)
def test_the_unreleased_contract_ships_an_entry_or_a_written_stanza(shipped, tmp_path, text):
    _changelog(shipped, text)
    r = _ship(shipped, tmp_path, "--wait", GOH_SHIP_CHANGELOG="unreleased")
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_unreleased_contract_refuses_an_empty_or_doubled_heading(shipped, tmp_path):
    for text in (
        "# C\n\n## Unreleased\n\n## v1.0.0\n\n- x\n",
        "# C\n\n## Unreleased\n\n- a\n\n## Unreleased\n\n- b\n",
    ):
        _changelog(shipped, text)
        r = _ship(shipped, tmp_path, "--wait", GOH_SHIP_CHANGELOG="unreleased")
        assert r.returncode == 2 and "Unreleased" in r.stderr, r.stdout + r.stderr


@pytest.mark.parametrize("value", ["", "cutt"])
def test_a_changelog_with_no_declared_or_a_bad_contract_is_refused(shipped, tmp_path, value):
    _changelog(shipped, "# C\n\n## Unreleased\n\n- an entry\n\n## v1.0.0\n\n- x\n")
    r = _ship(shipped, tmp_path, "--wait", GOH_SHIP_CHANGELOG=value)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "GOH_SHIP_CHANGELOG" in r.stderr, r.stderr
    assert not (tmp_path / "ship.json").exists()


def test_no_changelog_and_no_contract_ships(shipped, tmp_path):
    r = _ship(shipped, tmp_path, "--wait", GOH_SHIP_CHANGELOG="")
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_unreleased_entry_or_a_written_stanza_ships(shipped, tmp_path):
    _changelog(shipped, "# CHANGELOG\n\n## Unreleased\n\n## v1.1.0\n\n* by hand\n")
    r = _ship(shipped, tmp_path, "--wait", "--version", "1.1.0")
    assert r.returncode == 0, r.stdout + r.stderr
