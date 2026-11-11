"""`GOH_INSTALLS` names installable artifacts in a form the installed-version reader reads (`goh installs`).

A repo declares what it installs on the Mac in its `.gatesrc` (LOOP.INSTALLED, 2026-11-08): space-separated
artifacts, each an app bundle at `/Applications/...X.app` or `~/Applications/...X.app`, or a CLI by its bare
name. The reader (`.loop/installs.py`) resolves an app against /Applications or $HOME and a CLI against the
house bin dirs, so any other spelling (a relative bundle, `/usr/local/bin/x`, an empty value) is a
declaration that silently verifies nothing. A repo with no key is not judged.
"""

from __future__ import annotations

import pytest
from conftest import commit_all, run_goh, write

GOOD = (
    "/Applications/Widget.app widget",
    "/Applications/Antiknob/Antiknob.app",
    "~/Applications/TokenPool.app",
    "routines sys-updater app_updates",
)

BAD = (
    ("", "declares nothing"),
    ("Widget.app", "Widget.app"),
    ("/opt/Widget.app", "/opt/Widget.app"),
    ("/Applications/../tmp/X.app", "/Applications/../tmp/X.app"),
    ("/usr/local/bin/widget", "/usr/local/bin/widget"),
    ("widget widget", "twice"),
    ("-widget", "-widget"),
)


@pytest.mark.parametrize("value", GOOD)
def test_a_readable_declaration_passes(repo, value):
    write(repo, ".gatesrc", f"GOH_INSTALLS='{value}'\n")
    commit_all(repo)
    r = run_goh(repo, "installs")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("value, named", BAD)
def test_an_unreadable_declaration_is_red(repo, value, named):
    write(repo, ".gatesrc", f"GOH_INSTALLS='{value}'\n")
    commit_all(repo)
    r = run_goh(repo, "installs")
    assert r.returncode == 1, r.stdout + r.stderr
    assert named in r.stderr and "GOH_INSTALLS" in r.stderr, r.stderr


def test_no_key_is_not_judged(repo):
    r = run_goh(repo, "installs")
    assert r.returncode == 0, r.stdout + r.stderr


def test_it_is_a_structural_step(repo):
    write(repo, ".gatesrc", "GOH_INSTALLS='bin/widget'\n")
    commit_all(repo)
    r = run_goh(repo, "structural", "--full", "--keep-going")
    assert r.returncode != 0, r.stdout + r.stderr
    assert "bin/widget" in r.stdout + r.stderr
