"""Every temp root goh makes lives under `$TMPDIR/goh.noindex`, which Spotlight never indexes.

THE CASE (2026-11-17, EST.MDSCRAWL). The owner's fs_usage capture, 30 s during gates: 94% (337/359)
of the paths Spotlight's mds resolved were goh pytest temp trees (`$TMPDIR/goh-pytest.XXXXXX`),
half of them fixture .git/objects; mds read ~730 fsevents/s. A directory named `*.noindex` is never
indexed, so every root goes under ONE (`goh_tmp`: lib/tmp_root.sh, lib/tmp_root.py), the sweepers
still reach it, and tests/tmp_root_lint.py refuses a new mktemp in gates/, lib/, tools/ outside it.
"""

# goh-reads-staged: gates lib tools

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import tmp_root_lint as lint
from conftest import REPO_ROOT, hermetic_env
from test_claude_load_guard import _decide
from test_pytest_runner_basetemp import _run

sys.path.insert(0, str(REPO_ROOT / "lib"))
from prune_kept import backstop

NOINDEX = "goh.noindex"


@pytest.mark.load_sensitive
def test_a_pytest_run_root_lives_under_the_noindex_parent(tmp_path) -> None:
    done, base = _run(tmp_path, 3, tmpdir=tmp_path / "t")
    assert done.returncode == 3, done.stdout + done.stderr
    assert base.parent == tmp_path / "t" / NOINDEX, base
    (base / ".goh-owner").unlink()
    base.rmdir()


def test_the_helper_never_nests_a_second_noindex(tmp_path) -> None:
    lib = REPO_ROOT / "lib" / "tmp_root.sh"
    for tmpdir, want in ((tmp_path, tmp_path / NOINDEX), (tmp_path / NOINDEX, tmp_path / NOINDEX)):
        env = hermetic_env(TMPDIR=f"{tmpdir}/")
        r = subprocess.run(["bash", "-c", f'. "{lib}"; goh_tmp'], capture_output=True, text=True,
                           env=env, stdin=subprocess.DEVNULL, check=True)  # fmt: skip
        assert Path(r.stdout.strip()) == want and want.is_dir(), r.stdout


@pytest.mark.load_sensitive  # its clock: a root aged 13 h by utime, against the 12 h backstop
def test_the_backstop_still_finds_a_killed_runs_root_under_the_parent(tmp_path) -> None:
    stale = tmp_path / NOINDEX / "goh-pytest.ab12CD"
    stale.mkdir(parents=True)
    (stale / "x").write_text("x")
    old = time.time() - 13 * 3600
    for p in (stale / "x", stale):
        os.utime(p, (old, old))
    assert str(stale) in backstop(str(tmp_path))
    assert not stale.exists() and (tmp_path / NOINDEX).is_dir()


def test_a_sweep_of_the_parent_itself_is_refused(tmp_path) -> None:
    for command in ('rm -rf "$TMPDIR/goh.noindex"', "rm -rf ${TMPDIR:-/tmp}/goh.noindex/"):
        assert _decide(command, tmp_path) is not None, command


def test_the_lint_sees_each_bypass_and_passes_the_helper() -> None:
    """Calibration: a planted mktemp outside the helper is refused, the helper's spelling is not."""
    sh = (
        'a="$(mktemp "${TMPDIR:-/tmp}/goh-x.XXXXXX")"\n'  # 1
        'b="$(mktemp' + ' -d)"\n'  # 2 (split: test_temp_claimable.py would refuse it whole)
        "c=$(mktemp -t goh-c)\n"  # 3
        'd="$(mktemp -d /tmp/goh-d.XXXXXX)"\n'  # 4
        'e="$(mktemp -d "$(goh_tmp)/goh-e.XXXXXX")"\n'
        'f="$(mktemp "$cache/.build.XXXXXX")"\n'
        "# mktemp -d in a comment\n"
    )
    assert lint.shell_findings(sh) == [1, 2, 3, 4]
    py = (
        "import tempfile\n"
        "a = tempfile.mkdtemp(prefix='goh-a.')\n"  # 2
        "b = tempfile.mkdtemp(prefix='goh-b.', dir=goh_tmp())\n"
        "with tempfile.TemporaryDirectory(prefix='goh-c.') as c:\n"  # 4
        "    pass\n"
    )
    assert lint.python_findings(py) == [2, 4]


def test_no_temp_root_in_gates_lib_or_tools_bypasses_the_helper() -> None:
    assert lint.findings(REPO_ROOT) == [], "make it under goh_tmp (lib/tmp_root.sh / tmp_root.py)"
