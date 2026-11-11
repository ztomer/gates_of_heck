"""A release heading is a phrase that stands alone, never a fragment of an entry (AU.RELHEADING).

app_updates v1.59.1 shipped as `## v1.59.1 — , like every other config file _(2026-11-11)_`: the
cut stripped the first entry's bold lead (`**`autoinstall.toml` now lives in the config dir ...**`)
to derive a title and kept what followed it, ", like every other config file". Nothing asked
whether the result was a title at all. Now `derive_title` keeps the lead when the text after it is
no phrase, and `cut` and `check` refuse a heading that is a fragment.
"""

from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

CUT = REPO_ROOT / "tools" / "release-kit" / "changelog_cut.py"
TODAY = datetime.date.today().isoformat()
DATED_TAIL = "## v1.1.0 — older _(2026-01-01)_\n\n- older\n"

# The exact first entry of app_updates v1.59.1.
ENTRY_1591 = (
    "- **`autoinstall.toml` now lives in the config dir `APP_UPDATES_CONFIG_DIR`\n"
    "  names**, like every other config file. It was hard-coded to\n"
    "  `~/.config/app_updates`, so a run against a scratch config dir still read it.\n"
)


def _cut(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CUT), *args, "--file", str(path)],
                          capture_output=True, text=True, check=False,
                          stdin=subprocess.DEVNULL)  # fmt: skip


def _log(tmp_path: Path, text: str) -> Path:
    log = tmp_path / "CHANGELOG.md"
    log.write_text(text)
    return log


def test_the_1_59_1_entry_derives_a_title_that_is_a_phrase(tmp_path):
    log = _log(tmp_path, f"# C\n\n## Unreleased\n\n### Fixed\n{ENTRY_1591}\n{DATED_TAIL}")
    r = _cut(log, "cut", "--version", "1.59.1")
    assert r.returncode == 0, r.stderr
    want = (
        "\n## v1.59.1 — `autoinstall.toml` now lives in the config dir `APP_UPDATES_CONFIG_DIR` "
        f"names _({TODAY})_\n"
    )
    assert want in log.read_text(), log.read_text()


@pytest.mark.parametrize(
    "title",
    [
        ", like every other config file",
        "like every other config file,",
        "and,",
        "the `autoinstall.toml",
        "- a dash",
        "the (unclosed",
    ],
)
def test_a_cut_refuses_a_title_that_is_a_fragment(tmp_path, title):
    log = _log(tmp_path, f"# C\n\n## Unreleased\n\n- new\n\n{DATED_TAIL}")
    before = log.read_text()
    r = _cut(log, "cut", "--version", "1.2.0", "--title", title)
    assert r.returncode == 1 and "not a phrase" in r.stderr, r.stderr
    assert log.read_text() == before


def test_the_check_refuses_the_shipped_1_59_1_heading(tmp_path):
    log = _log(
        tmp_path,
        "# C\n\n## Unreleased\n\n## v1.59.1 — , like every other config file _(2026-11-11)_\n\n"
        f"### Fixed\n- x\n\n{DATED_TAIL}",
    )
    r = _cut(log, "check", "--version", "1.59.1")
    assert r.returncode == 1 and "not a phrase" in r.stderr, r.stderr


@pytest.mark.parametrize(
    "heading",
    [
        "## v1.2.0 -- the row carries the tool's own reason",
        "## v1.2.0 — `socks5h`: the lookup leaves with the connection _(2026-10-02)_",
        "## v1.2.0 — ElyOffice and Reframe resolve; three buttons stream _(2026-10-24)_",
        "## v1.2.0",
        "## v1.2.0 (2026-11-10)",
        "## [1.2.0] - 2026-01-01",
        "## v1.2.0 — a clean clone builds",
    ],
)
def test_a_phrase_or_a_bare_heading_passes_the_check(tmp_path, heading):
    log = _log(tmp_path, f"# C\n\n## Unreleased\n\n{heading}\n\n- x\n\n{DATED_TAIL}")
    r = _cut(log, "check", "--version", "1.2.0")
    assert r.returncode == 0, r.stderr


# ---- GOH.SHIPSUMMARY: a clause clipped at TITLE_MAX is no title ---------------------------------

ENTRY_096 = (
    "* **goh ship releases goh itself: tools/release_goh.sh (GOH.SHIPKIT).** goh declared no\n"
    "  GOH_SHIP_RELEASE, so `goh ship` refused it and v0.95.0's bump was made by hand. The kit\n"
    "  refuses a dirty tree.\n"
)
LONG_096 = (
    "* goh declared no GOH_SHIP_RELEASE, so `goh ship` refused it and v0.95.0's bump was made by\n"
    "  hand. The kit refuses a dirty tree.\n"
)
GOH_TAIL = "## v0.95.0 -- older\n\n* older\n"


def test_a_lead_that_is_its_own_sentence_is_the_title(tmp_path):
    """v0.96.0's entry: the bold lead is a sentence, the text after it the explanation."""
    log = _log(tmp_path, f"# C\n\n## Unreleased\n\n{ENTRY_096}\n{GOH_TAIL}")
    r = _cut(log, "cut", "--version", "0.96.0")
    assert r.returncode == 0, r.stderr
    assert "\n## v0.96.0 -- goh ship releases goh itself\n" in log.read_text(), log.read_text()


def test_a_cut_refuses_a_clause_clipped_to_fit(tmp_path):
    log = _log(tmp_path, f"# C\n\n## Unreleased\n\n{LONG_096}\n{GOH_TAIL}")
    before = log.read_text()
    r = _cut(log, "cut", "--version", "0.96.0")
    assert r.returncode == 1 and "past 90" in r.stderr and "--title" in r.stderr, r.stderr
    assert log.read_text() == before
    assert _cut(log, "ready", "--version", "0.96.0").returncode == 1
    assert _cut(log, "ready", "--version", "0.96.0", "--title", "goh ships itself").returncode == 0
    r = _cut(log, "cut", "--version", "0.96.0", "--title", "goh ships itself")
    assert r.returncode == 0, r.stderr
