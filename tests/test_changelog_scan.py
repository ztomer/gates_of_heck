"""goh ship refuses a release text goh's scanners would refuse, BEFORE the release gate
(GOH.SHIPTITLESCAN).

v0.140.3's first release attempt wrote a CHANGELOG.md heading from `goh ship --summary` text that
held a home path; the release step's full push gate counted it (tests/test_self_walk.py, `no
hard-coded home paths` 136 -> 137) 1019 s in. The text is known at the start of the ship: its
title (`--summary`) and the entries the cut moves out of Unreleased, fragments folded in. It is read
by the binary's own scanners (tools/release-kit/changelog_scan.py): seconds, nothing written.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git, hermetic_env, native_goh_path as goh_bin
from test_ship import _changelog, _ship, make_shipped
from timing_bounds import assert_sooner, patience

CUT = REPO_ROOT / "tools" / "release-kit" / "changelog_cut.py"
# Built from pieces: a literal here is a finding of the very scanners under test.
HOME = "/Us" + "ers/someone/Projects/x"
TILDE = "~/Proj" + "ects/.loop"
FACE = "\N{GRINNING FACE}"
FOUND = "# CHANGELOG\n\n## Unreleased\n\n* a real change\n\n## v1.0.0 -- the first\n\n* the first\n"


@pytest.fixture
def shipped(repo: Path, tmp_path: Path) -> Path:
    return make_shipped(repo, tmp_path)


def _ready(tmp_path: Path, text: str, *args: str,
           binary: str | None = None) -> tuple[subprocess.CompletedProcess, float]:  # fmt: skip
    log = tmp_path / "CHANGELOG.md"
    log.write_text(text)
    env = hermetic_env(GOH_BIN=binary or str(goh_bin()))
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, str(CUT), "ready", "--version", "1.1.0", *args,
                        "--file", str(log)], capture_output=True, text=True, check=False,
                       env=env, stdin=subprocess.DEVNULL, timeout=patience(120))  # fmt: skip
    return r, time.monotonic() - t0


@pytest.mark.load_sensitive
def test_a_clean_release_text_is_ready(tmp_path):
    """Calibration: the instrument can say yes, or its refusals below mean nothing."""
    r, _ = _ready(tmp_path, FOUND, "--title", "a real change")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.load_sensitive
def test_a_scan_that_cannot_run_is_a_refusal_never_a_clean_text(tmp_path):
    """A binary that is not there read as 'no findings' would be the scan that stopped reading."""
    r, _ = _ready(tmp_path, FOUND, "--title", "fine", binary=str(tmp_path / "no-such-goh"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no native goh binary" in r.stderr and "no-such-goh" in r.stderr, r.stderr


@pytest.mark.load_sensitive
@pytest.mark.parametrize(
    ("title", "label"),
    [
        (f"quoted {TILDE} in a title", "no hard-coded home paths"),
        (f"the path {HOME} in a title", "no hard-coded home paths"),
        (f"a face {FACE} in a title", "no disallowed emoji"),
    ],
)
def test_a_title_the_scanners_refuse_is_refused_naming_scanner_and_line(tmp_path, title, label):
    r, took = _ready(tmp_path, FOUND, "--title", title)
    assert r.returncode == 1, r.stdout + r.stderr
    assert label in r.stderr and "line 1 of the release text" in r.stderr, r.stderr
    assert_sooner(took, 2, 1000, "the refusal of a bad title")
    assert (tmp_path / "CHANGELOG.md").read_text() == FOUND  # nothing written, nothing to undo


@pytest.mark.load_sensitive
def test_an_unreleased_entry_the_scanners_refuse_names_its_line(tmp_path):
    text = FOUND.replace("* a real change", f"* a real change\n* then {HOME} again")
    r, _ = _ready(tmp_path, text, "--title", "fine")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no hard-coded home paths: line 5 of the release text" in r.stderr, r.stderr
    assert "then" in r.stderr  # the line's own text is quoted


@pytest.mark.load_sensitive
def test_a_fragment_the_scanners_refuse_is_refused(tmp_path):
    frags = tmp_path / "changelog"
    frags.mkdir()
    (frags / "20261120-a.md").write_text(f"* a fragment quoting {TILDE}\n")
    r, took = _ready(tmp_path, FOUND, "--title", "fine", "--fragments", str(frags))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no hard-coded home paths" in r.stderr and "a fragment quoting" in r.stderr, r.stderr
    assert_sooner(took, 2, 1000, "the refusal of a bad fragment")


@pytest.mark.load_sensitive
def test_goh_ship_refuses_a_summary_with_a_home_path_before_the_push(shipped, tmp_path):
    _changelog(shipped, FOUND)
    before = git(shipped, "ls-remote", "origin")
    t0 = time.monotonic()
    r = _ship(shipped, tmp_path, "--wait", "--summary", f"fixed {TILDE}")
    took = time.monotonic() - t0
    assert r.returncode == 2, r.stdout + r.stderr
    assert "no hard-coded home paths" in r.stderr and "line 1 of the release text" in r.stderr
    assert_sooner(took, 5, 1000, "goh ship refusing a bad summary")
    assert git(shipped, "ls-remote", "origin") == before  # nothing pushed
    assert git(shipped, "status", "--porcelain", "--untracked-files=no") == ""


def test_goh_ship_refuses_a_fragment_with_a_home_path_before_the_push(shipped, tmp_path):
    _changelog(shipped, FOUND.replace("* a real change\n\n", ""))
    (shipped / "changelog").mkdir()
    (shipped / "changelog" / "20261120-a.md").write_text(f"* quoted {HOME}\n")
    git(shipped, "add", "changelog")
    git(shipped, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "docs: frag")
    before = git(shipped, "ls-remote", "origin")
    r = _ship(shipped, tmp_path, "--wait", "--summary", "fine",
              GOH_CHANGELOG_FRAGMENTS="on")  # fmt: skip
    assert r.returncode == 2, r.stdout + r.stderr
    assert "no hard-coded home paths" in r.stderr, r.stderr
    assert git(shipped, "ls-remote", "origin") == before
