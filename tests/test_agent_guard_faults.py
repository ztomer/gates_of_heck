"""report_faults.faults: the `Faults seen:` line as a list (split from test_claude_agent_guard.py).

GOH.FAULTSPLIT: only a TOP-LEVEL ';' separates faults; the rest are recorded exactly as written.
"""

from __future__ import annotations

import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "hooks" / "claude"))
import report_faults


def test_faults_none_is_an_empty_list() -> None:
    assert report_faults.faults("Status: landed\nFaults seen: none") == []


def test_faults_several_are_split_on_semicolons() -> None:
    report = "Status: landed\nabc1234 x\nFaults seen: WARN disk on .33 health; /srv/t not g+w (.90)"
    assert report_faults.faults(report) == ["WARN disk on .33 health", "/srv/t not g+w (.90)"]


def test_faults_are_recorded_exactly_as_written() -> None:
    """G15: a leading backtick was stripped from the first fault (only whitespace is trimmed)."""
    report = "Faults seen: `tools/land.sh` printed landed; second `one`\n"
    assert report_faults.faults(report) == ["`tools/land.sh` printed landed", "second `one`"]
    assert report_faults.faults("Faults seen:   `x` at y  ") == ["`x` at y"]


def test_faults_a_semicolon_inside_brackets_or_quotes_is_not_a_separator() -> None:
    """GOH.FAULTSPLIT: the two real lines of 2026-11-09 each became two rows, which the loop then
    triaged as separate faults. Only a TOP-LEVEL ';' separates faults."""
    a = "a targeted tools/pytest.sh run ... (only in my side run; the land gate passed)"
    b = '... ("cannot lock ref HEAD"; tree-stamp refusal ...)'
    assert report_faults.faults(f"Faults seen: {a}") == [a]
    assert report_faults.faults(f"Faults seen: {b}") == [b]
    assert report_faults.faults(f"Faults seen: {a}; {b}") == [a, b]
    assert report_faults.faults("Faults seen: x [a; b] y; `c; d`; \"e; f\" g; h") == [
        "x [a; b] y", "`c; d`", '"e; f" g', "h",
    ]  # fmt: skip


def test_faults_an_apostrophe_or_an_unbalanced_opener_does_not_swallow_the_rest() -> None:
    """Prose has apostrophes ("don't") and agents drop a closer: neither may merge two faults
    into one, nor lose one -- an unbalanced line falls back to the plain split."""
    assert report_faults.faults("Faults seen: it doesn't build; second") == [
        "it doesn't build",
        "second",
    ]
    assert report_faults.faults("Faults seen: opened (never closed; second") == [
        "opened (never closed",
        "second",
    ]
    assert report_faults.faults("Faults seen: closed) early; second") == ["closed) early", "second"]


def test_faults_a_backticked_none_is_still_none() -> None:
    assert report_faults.faults("Faults seen: `none`") == []


def test_faults_missing_line_is_null_not_none() -> None:
    assert report_faults.faults("Status: landed\nall good") is None


# LOOP.FAULTBULLETS: EST.STORE1116's report (agent afed67cbfa217e5d4, 2026-11-13 07:04Z), its
# Faults section as it was written. Read from the line alone it was recorded as NO faults.
STORE1116 = (
    "Status: landed (ship started, not yet verified)\n"
    "\n"
    "- Left open: the ship's push and CI verdict (not watched).\n"
    "\n"
    "Faults seen:\n"
    "- The brief said to put the entry in the new version's stanza. `changelog_cut.py "
    "cut` refuses an empty Unreleased even when that stanza exists, so I kept the entries"
    " under Unreleased for the ship to cut. That is the DV.SHIPCUT area.\n"
    "- `scripts/check_linux_all_features.sh` failed once: `minilzo-rs` build script "
    '"Permission denied" because '
    "`~/.cache/divoom-linux-lint/target/debug/build/minilzo-rs-*/build-script-build` had "
    "lost its exec bit (cause unknown). I deleted the two stale `minilzo-rs-*` build dirs"
    " there, and the step then passed.\n"
    "- `goh crates-behind` still warns of 16 duplicate-major transitive crates (bitflags,"
    " objc2, thiserror and others), upstream-determined. Not fixed.\n"
    "- The store's `crates_behind_seed.json` still holds an entry for the untracked clone"
    " `divoom-control/references/divoom-ditoo-pro-controller`. It is not a divoom-control"
    " root and the seed kind is retired, so I left it."
)


def test_faults_listed_as_bullets_under_the_line_are_each_one_fault() -> None:
    found = report_faults.faults(STORE1116)
    assert found is not None and len(found) == 4, found
    assert found[0].startswith("The brief said") and found[0].endswith("DV.SHIPCUT area.")
    assert found[1].startswith("`scripts/check_linux_all_features.sh` failed once")
    assert "16 duplicate-major" in found[2] and "divoom-ditoo-pro-controller" in found[3]


def test_faults_bullets_stop_at_a_blank_line_and_take_indented_continuations() -> None:
    report = "Faults seen:\n\n* one\n  wrapped on\n2. two\n\nNext: not a fault\n- nor this"
    assert report_faults.faults(report) == ["one wrapped on", "two"]
    assert report_faults.faults("Faults seen: on the line\n- and below") == [
        "on the line",
        "and below",
    ]
    assert report_faults.faults("Faults seen: none\n\n- an unrelated list") == []


def test_faults_an_empty_line_with_no_list_is_not_stated_and_warns() -> None:
    for report in ("Status: landed\nFaults seen:\nI saw a few things, see above.", "Faults seen:"):
        found, warning = report_faults.read_faults(report)
        assert found is None, report  # never [] -- that is the claim "none"
        assert warning and "one `- ` bullet per fault" in warning, warning
    assert report_faults.read_faults("Faults seen: none") == ([], None)
