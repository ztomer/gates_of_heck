"""changelog_cut.py and release step 0b make the stanza in the house heading, from whatever
Unreleased holds (GOH.CUTTITLE).

Four defects met cutting ztools v3.13.1/v3.13.2 and routines:

* a kit cut wrote a bare `## vX` in repos whose headings are `## vX — title _(YYYY-MM-DD)_`: the
  title is the release's --title or, failing that, the first Unreleased entry, and the date is the
  release day;
* a second `## Unreleased` heading (written under the empty one a cut leaves) made the cut read the
  EMPTY first section and decline, then left an empty duplicate heading behind: Unreleased is ONE
  section, a cut folds every Unreleased section into one stanza and leaves exactly one heading;
* a hand-written stanza next to entries still under Unreleased was refused: the entries are merged
  into the stanza, by sub-heading, and the ones it already holds are not repeated.
"""

from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

import test_release_kit as rk
from conftest import REPO_ROOT
from test_release_kit import TAG, VERSION, commit_all, run_release, sh

kit = rk.kit  # the release fixtures, by assignment (an import would be shadowed by each test's arg)

CUT = REPO_ROOT / "tools" / "release-kit" / "changelog_cut.py"
TODAY = datetime.date.today().isoformat()

DATED_TAIL = "## v1.1.0 — older _(2026-01-01)_\n\n- older\n"


def _cut(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CUT), *args, "--file", str(path)],
                          capture_output=True, text=True, check=False,
                          stdin=subprocess.DEVNULL)  # fmt: skip


def _log(tmp_path: Path, text: str) -> Path:
    log = tmp_path / "CHANGELOG.md"
    log.write_text(text)
    return log


def _release_with(kit: dict, text: str, subject: str = f"chore: version {VERSION}") -> str:
    (kit["proj"] / "CHANGELOG.md").write_text(text)
    commit_all(kit["proj"], subject)
    return text


# --- the heading: title and date ------------------------------------------------------------


def test_the_kit_cut_carries_the_release_title_and_day_in_a_dated_repo(kit):
    _release_with(kit, f"# C\n\n## Unreleased\n\n- new thing\n\n{DATED_TAIL}")
    r = run_release(kit, "--title", "the new thing")
    assert r.returncode == 0, r.stdout + r.stderr
    text = (kit["proj"] / "CHANGELOG.md").read_text()
    assert f"## Unreleased\n\n## v1.2.3 — the new thing _({TODAY})_\n\n- new thing\n" in text, text


def test_the_kit_cut_takes_the_first_entry_for_a_title_when_none_is_given(kit):
    _release_with(
        kit,
        "# C\n\n## Unreleased\n\n### Fixed\n"
        "- The on-device tier asks `fm` at a laxer level (it refused two of three timelines).\n"
        f"  More.\n\n{DATED_TAIL}",
    )
    r = run_release(kit)
    assert r.returncode == 0, r.stdout + r.stderr
    text = (kit["proj"] / "CHANGELOG.md").read_text()
    want = f"## v1.2.3 — the on-device tier asks `fm` at a laxer level _({TODAY})_\n"
    assert want in text, text


def test_a_file_whose_stanzas_carry_no_title_gets_a_bare_cut(kit):
    _release_with(kit, "# C\n\n## Unreleased\n\n- new\n\n## v1.1.0\n\n- older\n")
    assert run_release(kit).returncode == 0
    assert "\n## v1.2.3\n\n- new\n" in (kit["proj"] / "CHANGELOG.md").read_text()


def test_a_run_of_undated_hand_headings_does_not_unlearn_the_date(tmp_path):
    """ztools' last two stanzas were hand-written `## vX -- title` with no day; the dated ones
    behind them are the house heading, and a cut follows THAT."""
    log = _log(
        tmp_path,
        "# C\n\n## Unreleased\n\n- new\n\n## v1.3.0 -- b\n\n- b\n\n## v1.2.0 -- a\n\n- a\n\n"
        "## v1.1.0 — older _(2026-01-01)_\n\n- older\n",
    )
    assert _cut(log, "cut", "--version", "1.4.0", "--title", "t").returncode == 0
    assert f"\n## v1.4.0 — t _({TODAY})_\n" in log.read_text()


def test_the_derived_title_is_one_short_clause(tmp_path):
    log = _log(
        tmp_path,
        "# C\n\n## Unreleased\n\n"
        "- **Fix**: the summary tier stops refusing timelines (it asked at the default level).\n\n"
        f"{DATED_TAIL}",
    )
    assert _cut(log, "cut", "--version", "1.2.0").returncode == 0
    assert (
        f"\n## v1.2.0 — the summary tier stops refusing timelines _({TODAY})_\n" in log.read_text()
    )


# --- one Unreleased section -----------------------------------------------------------------

TWICE = (
    "# C\n\n## Unreleased\n\n## Unreleased\n\n### Fixed\n- the fix\n\n## v1.1.0 -- older\n\n- o\n"
)


def test_a_cut_reads_every_unreleased_section_and_leaves_one_heading(tmp_path):
    log = _log(tmp_path, TWICE)
    r = _cut(log, "cut", "--version", "1.2.0", "--title", "t")
    assert r.returncode == 0, r.stderr
    text = log.read_text()
    assert text.count("## Unreleased") == 1, text
    assert text.startswith("# C\n\n## Unreleased\n\n## v1.2.0 -- t\n\n### Fixed\n- the fix\n"), text


def test_entries_in_two_unreleased_sections_are_both_moved(tmp_path):
    log = _log(
        tmp_path,
        "# C\n\n## Unreleased\n\n- one\n\n## Unreleased\n\n- two\n\n## v1.1.0 -- o\n\n- o\n",
    )
    assert _cut(log, "cut", "--version", "1.2.0", "--title", "t").returncode == 0
    text = log.read_text()
    assert text.count("## Unreleased") == 1 and "- one\n" in text and "- two\n" in text, text


def test_the_kit_cuts_when_the_unreleased_entries_sit_under_a_second_heading(kit):
    """The ztools shape: the empty heading a cut left, then entries under a second one."""
    _release_with(kit, TWICE.replace("v1.1.0 -- older", "v1.1.0 — older _(2026-01-01)_"))
    r = run_release(kit)
    assert r.returncode == 0, r.stdout + r.stderr
    text = (kit["proj"] / "CHANGELOG.md").read_text()
    assert text.count("## Unreleased") == 1 and "## v1.2.3 — the fix" in text, text
    assert sh(kit["proj"], "status", "--porcelain").strip() == ""


def test_a_second_unreleased_heading_fails_the_check(tmp_path):
    log = _log(tmp_path, "# C\n\n## Unreleased\n\n## Unreleased\n\n## v1.2.0 -- t\n\n- x\n")
    r = _cut(log, "check", "--version", "1.2.0")
    assert r.returncode == 1 and "Unreleased" in r.stderr and "twice" in r.stderr, r.stderr


# --- a hand-written stanza with entries still under Unreleased ------------------------------


def test_unreleased_entries_are_merged_into_a_hand_written_stanza(tmp_path):
    log = _log(
        tmp_path,
        "# C\n\n## Unreleased\n\n### Fixed\n- late fix\n\n### Added\n- late add\n\n"
        "## v1.2.0 -- mine\n\n### Fixed\n- early fix\n\n## v1.1.0 -- o\n\n- o\n",
    )
    r = _cut(log, "cut", "--version", "1.2.0")
    assert r.returncode == 0, r.stderr
    want = (
        "# C\n\n## Unreleased\n\n## v1.2.0 -- mine\n\n"
        "### Fixed\n- early fix\n- late fix\n\n### Added\n- late add\n\n## v1.1.0 -- o\n"
    )
    assert log.read_text().startswith(want), log.read_text()
    assert _cut(log, "check", "--version", "1.2.0").returncode == 0


def test_a_merge_does_not_repeat_an_entry_the_stanza_holds(tmp_path):
    log = _log(
        tmp_path,
        "# C\n\n## Unreleased\n\n- same\n  entry\n- late\n\n## v1.2.0 -- mine\n\n- same entry\n\n"
        "## v1.1.0 -- o\n\n- o\n",
    )
    assert _cut(log, "cut", "--version", "1.2.0").returncode == 0
    text = log.read_text()
    assert text.count("same") == 1 and "- late\n" in text, text


def test_the_kit_merges_instead_of_refusing_a_hand_written_stanza(kit):
    _release_with(
        kit,
        f"# C\n\n## Unreleased\n\n- shipped late\n\n## {TAG}\n\n- hand written\n\n## v1.1.0\n\n- o\n",
        "feat: something",
    )
    r = run_release(kit)
    assert r.returncode == 0, r.stdout + r.stderr
    text = sh(kit["proj"], "show", f"{TAG}:CHANGELOG.md")
    assert f"## Unreleased\n\n## {TAG}\n\n- hand written\n- shipped late\n" in text, text
    assert sh(kit["proj"], "log", "-1", "--format=%s").strip() == f"chore: changelog {TAG}"


def test_a_bold_lead_holding_an_asterisk_is_still_the_title(tmp_path):
    """goh v0.106.1's ship was refused: its lead `**... (GOH.ROADMAPREQ, RM.*).**` holds a `*`, the
    lead pattern `[^*]+` missed it, and the derived title kept the bold markers
    ('**The roadmap pending list is gone'), which reads as opening on punctuation."""
    entry = "**The roadmap list is gone (GOH.X, RM.*).** Every repo declares it."
    log = _log(tmp_path, f"# C\n\n## Unreleased\n\n* {entry}\n\n{DATED_TAIL}")
    r = _cut(log, "ready", "--version", "1.2.0")
    assert r.returncode == 0, r.stderr
    assert _cut(log, "cut", "--version", "1.2.0").returncode == 0
    assert f"\n## v1.2.0 — the roadmap list is gone _({TODAY})_\n" in log.read_text()
