"""The release kit tags only a commit that IS the release (GOH.REL2).

* the version: goh's v0.63.0 was tagged on its changelog commit, BEFORE the commit that bumped
  Cargo.toml; only the pre-push tag check refused it, after the tag existed. release.sh now asks
  the same check (`goh tag-version`, the repo's .gatesrc in its environment) first.
* the changelog: goh's Unreleased carried twelve entries v0.60.0-v0.63.0 had shipped. At the tagged
  commit Unreleased must be empty and no entry may repeat an earlier stanza's;
  `changelog_cut.py cut` makes the stanza FROM Unreleased.
* the backfill floor (ZT.CHLOG): ztools warned every release about 56 pre-v2.2.0 tags its
  CHANGELOG deliberately excludes; `GOH_RELEASE_BACKFILL_FLOOR` in .gatesrc bounds the backfill.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import test_release_kit as rk
from conftest import REPO_ROOT
from test_release_kit import STANZA_BODY, TAG, VERSION, commit_all, gh_creates, run_release, sh

kit = rk.kit  # the release fixtures, by assignment (an import would be shadowed by each test's arg)
hand_tags = rk.hand_tags

CUT = REPO_ROOT / "tools" / "release-kit" / "changelog_cut.py"


def _tagged(kit: dict) -> bool:
    return bool(sh(kit["proj"], "tag", "-l", TAG).strip())


def _origin_tags(kit: dict) -> str:
    return sh(kit["origin"], "tag", "-l")


# --- the version ----------------------------------------------------------------------------


def test_a_head_that_does_not_declare_the_version_is_refused_before_anything(kit):
    (kit["proj"] / "VERSION").write_text("1.2.2\n")  # the bump is not committed yet
    commit_all(kit["proj"], "changelog: stanza")
    r = run_release(kit)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "step 'version' failed" in out and "1.2.2" in out, out
    assert not _tagged(kit) and TAG not in _origin_tags(kit)
    assert not kit["gh_log"].exists(), "CI was asked about a commit that cannot be released"


def test_the_bump_committed_the_same_release_tags(kit):
    (kit["proj"] / "VERSION").write_text("1.2.2\n")
    commit_all(kit["proj"], "changelog: stanza")
    assert run_release(kit).returncode != 0
    (kit["proj"] / "VERSION").write_text(f"{VERSION}\n")
    commit_all(kit["proj"], f"release: {TAG}")
    r = run_release(kit)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _tagged(kit) and TAG in _origin_tags(kit)


def test_the_repos_declared_version_source_is_read(kit):
    """The v0.63.0 shape: the version lives in Cargo.toml, named by .gatesrc."""
    proj = kit["proj"]
    (proj / "VERSION").unlink()
    (proj / ".gatesrc").write_text('GOH_TAG_VERSION_SOURCES="cargo:Cargo.toml"\n')
    (proj / "Cargo.toml").write_text('[package]\nname = "p"\nversion = "1.2.2"\n')
    commit_all(proj, "changelog: stanza")
    r = run_release(kit)
    assert r.returncode != 0 and "step 'version' failed" in r.stderr, r.stdout + r.stderr
    (proj / "Cargo.toml").write_text(f'[package]\nname = "p"\nversion = "{VERSION}"\n')
    commit_all(proj, "bump")
    r = run_release(kit)
    assert r.returncode == 0, r.stdout + r.stderr


# --- the changelog --------------------------------------------------------------------------


def _changelog(kit: dict, text: str) -> None:
    (kit["proj"] / "CHANGELOG.md").write_text(text)
    commit_all(kit["proj"], "changelog")


def test_an_unreleased_section_still_holding_entries_is_refused(kit):
    _changelog(kit, f"# C\n\n## Unreleased\n\n* shipped long ago\n\n## {TAG}\n\n{STANZA_BODY}\n")
    r = run_release(kit)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "step 'changelog' failed" in r.stderr and "Unreleased" in r.stderr, r.stderr
    assert not _tagged(kit)


def test_an_empty_unreleased_section_tags(kit):
    _changelog(kit, f"# C\n\n## Unreleased\n\n## {TAG}\n\n{STANZA_BODY}\n")
    r = run_release(kit)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_stanza_repeating_an_earlier_entry_is_refused(kit):
    _changelog(kit, f"# C\n\n## {TAG}\n\n* the same\n  entry\n\n## v1.1.0\n\n* the  same entry\n")
    r = run_release(kit)
    assert r.returncode != 0 and "duplicate entry" in r.stderr, r.stdout + r.stderr
    assert "v1.1.0" in r.stderr and not _tagged(kit)


def _cut(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CUT), *args, "--file", str(path)],
                          capture_output=True, text=True, check=False,
                          stdin=subprocess.DEVNULL)  # fmt: skip


def test_cut_moves_unreleased_into_the_new_stanza(tmp_path):
    log = tmp_path / "CHANGELOG.md"
    log.write_text("# C\n\n## Unreleased\n\n* new thing\n  more\n\n## v1.1.0\n\n* old\n")
    r = _cut(log, "cut", "--version", "1.2.0", "--title", "the new thing")
    assert r.returncode == 0, r.stderr
    text = log.read_text()
    assert text.startswith("# C\n\n## Unreleased\n\n## v1.2.0 -- the new thing\n\n* new thing\n")
    assert _cut(log, "check", "--version", "1.2.0").returncode == 0
    # ...and again: Unreleased is empty and the stanza exists, so a second cut is refused.
    assert _cut(log, "cut", "--version", "1.3.0", "--title", "x").returncode == 1


def test_a_v_prefixed_version_is_the_same_version(tmp_path):
    """`--version v1.2.0` is the tag spelling of 1.2.0: it cut a traceback, not a stanza."""
    log = tmp_path / "CHANGELOG.md"
    log.write_text("# C\n\n## Unreleased\n\n* new thing\n")
    r = _cut(log, "cut", "--version", "v1.2.0", "--title", "t")
    assert r.returncode == 0 and "Traceback" not in r.stderr, r.stderr
    assert "\n## v1.2.0 -- t\n" in log.read_text()
    assert _cut(log, "check", "--version", "v1.2.0").returncode == 0


def test_cut_refuses_an_entry_an_earlier_stanza_holds(tmp_path):
    log = tmp_path / "CHANGELOG.md"
    before = "# C\n\n## Unreleased\n\n* old\n\n## v1.1.0\n\n* old\n"
    log.write_text(before)
    r = _cut(log, "cut", "--version", "1.2.0", "--title", "t")
    assert r.returncode == 1 and "already in v1.1.0" in r.stderr, r.stderr
    assert log.read_text() == before


# --- the backfill floor ---------------------------------------------------------------------


def _floor(kit: dict, floor: str) -> None:
    (kit["proj"] / ".gatesrc").write_text(f"GOH_RELEASE_BACKFILL_FLOOR={floor}\n")
    commit_all(kit["proj"], "floor")


def test_tags_below_the_floor_are_neither_backfilled_nor_warned_about(hand_tags):
    _floor(hand_tags, "v1.1.0")
    r = run_release(hand_tags)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "v1.0.0" not in out, "a tag below the floor was warned about"
    assert [c.split()[3] for c in gh_creates(hand_tags)] == ["v1.1.0", TAG]


def test_the_floor_bounds_the_dry_run_plan_too(hand_tags):
    _floor(hand_tags, "v1.2.0")
    r = run_release(hand_tags, "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not [ln for ln in r.stdout.splitlines() if "[dry-run]" in ln and "v1.1.0 " in ln]


def test_a_malformed_floor_is_refused(hand_tags):
    _floor(hand_tags, "two-point-two")
    r = run_release(hand_tags, "--dry-run")
    assert r.returncode != 0 and "GOH_RELEASE_BACKFILL_FLOOR" in r.stderr, r.stdout + r.stderr
