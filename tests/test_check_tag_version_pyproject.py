"""check_tag_version.py — the PEP 621 `pyproject.toml` layout, on its own.

Every Python distribution declares its version under `[project]`, and no strategy in this table
could read it: `cargo:` matches `[package] version =`, which is Cargo's table, so it correctly
returns nothing for a `pyproject.toml`. The consequence is the same one an Apple bundle had --
measured on `game_asset_factory`, `refs/tags/v0.4.0` reported "NO version source declares a
version at this commit" while `pyproject.toml` plainly said `version = "0.4.0"`.

Anchored to the SECTION as well as the key, for the reason `from_cargo` is: a `version =` line
under `[tool.something]` is not the distribution's version, and reading it would either miss the
real declaration or invent a second one to disagree with.
"""

import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "checks"))
import _version_sources as sources

PEP621 = """[project]
name = "thing"
version = "1.4.2"
dependencies = []

[project.optional-dependencies]
dev = []

[tool.ruff]
line-length = 100
version = "9.9.9"
"""

POETRY = """[tool.poetry]
name = "thing"
version = "1.4.2"

[tool.poetry.dependencies]
python = "^3.13"
"""


def test_it_reads_the_project_section():
    assert sources.from_pyproject(PEP621) == [("(project)", "1.4.2")]


def test_a_version_under_another_table_is_not_the_distribution_version():
    """The `[tool.ruff] version = "9.9.9"` in the fixture is the trap this anchoring exists for.

    Unanchored, a `version =` match would return both numbers and the gate would report a correct
    release as disagreeing with itself.
    """
    found = sources.from_pyproject(PEP621)
    assert all(version != "9.9.9" for _table, version in found), found


def test_it_reads_the_poetry_spelling_too():
    """The other live spelling. Two spellings disagreeing is exactly what this gate is for."""
    assert sources.from_pyproject(POETRY) == [("(tool.poetry)", "1.4.2")]


def test_two_disagreeing_spellings_are_both_returned():
    text = '[project]\nversion = "1.0.0"\n\n[tool.poetry]\nversion = "2.0.0"\n'
    assert len(sources.from_pyproject(text)) == 2


def test_a_pyproject_with_no_version_declares_nothing():
    assert sources.from_pyproject('[project]\nname = "thing"\n') == []


def test_the_cargo_strategy_correctly_reads_nothing_here():
    """The two must not overlap. `cargo:` matching a `pyproject.toml` would be a false disagreement."""
    assert sources.from_cargo(PEP621) == []


def test_it_is_registered_and_configurable():
    assert "pyproject" in sources.KINDS
    assert sources.STRATEGIES["pyproject"] is sources.from_pyproject


@pytest.mark.parametrize(
    "mutate,expect_empty",
    [
        (lambda t: t.replace('version = "1.4.2"', 'version = "1.4"'), True),
        (lambda t: t.replace('version = "1.4.2"', 'version = "1.4.2-dev"'), False),
    ],
)
def test_value_shape_is_whatever_a_release_number_is(mutate, expect_empty):
    """`1.4` is not a release number; `1.4.2-dev` is one, and SEMVER_VALUE is the shared judge."""
    found = sources.from_pyproject(mutate(PEP621))
    assert (found == []) is expect_empty
