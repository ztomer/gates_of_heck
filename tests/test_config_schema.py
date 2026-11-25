"""Config-schema drift gate: the GOH_* keys the code reads and the keys docs/config/ declares agree.

Red-proof: add `GOH_SOMETHING_NEW` to any gate without a docs/config/GOH_SOMETHING_NEW.md and this
test goes red naming it -- at COMMIT, not only at push: the lines below declare that this file reads
the staged sources, so gates/staged_guards.sh runs it over the index whenever a commit stages a file
it scans (GOH.CONFIGKEYCOMMIT; tests/test_staged_guards.py keeps the list equal to
lib/config_ref.py SCAN). Until GOH.CONFIGMD it asked whether a key was a SUBSTRING of one page: 25
keys the code reads had no row. Each refusal is held on a fixture by tests/test_goh_config.py.
"""

# goh-reads-staged: gates checks lib tools hooks tui install.sh crates scripts bin .github
# goh-reads-staged: docs/config .gatesrc.example tests/_*.py tests/conftest.py

from __future__ import annotations

import pytest
from conftest import REPO_ROOT

from lib import config_ref


@pytest.fixture(scope="module")
def faults() -> list[str]:
    return config_ref.faults(REPO_ROOT)


def _said(faults: list[str], *words: str) -> str:
    return "\n".join(f for f in faults if any(w in f for w in words))


def test_config_schema_covers_keys(faults):
    """Every GOH_* key the code reads, the Rust crates included (BACKLOG 1.3: a key read only by the
    binary used to ship undocumented), is declared in docs/config/, as a whole word."""
    said = _said(faults, "declared nowhere", "PLACEHOLDERS")
    assert not said, f"undeclared GOH_* keys:\n{said}"


def test_every_declared_key_is_read(faults):
    """A declaration leaves with its last reader; one whose reader is not here names it."""
    said = _said(faults, "no code reads")
    assert not said, f"dead GOH_* keys:\n{said}"


def test_gatesrc_example_keys_are_documented(faults):
    """Starter template must not invent keys the schema does not know."""
    said = _said(faults, ".gatesrc.example")
    assert not said, said


def test_every_declaration_keeps_the_form_and_nothing_else_is_refused(faults):
    assert faults == [], "\n".join(faults)
