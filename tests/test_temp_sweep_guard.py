"""A recursive rm of a GLOB over goh's temp roots is refused at the tool boundary (GOH.FLAKYENVCOV).

THE CASE (2026-11-14, ~21:45). A lane agent cleaning up after itself ran
`rm -rf $TMPDIR/goh-pytest.*/`. Every gate's pytest has a temp root of its own
(`gates/_pytest_root.sh`: `$TMPDIR/goh-pytest.XXXXXX`, one per run, and goh's own pruning spares
a root whose `.goh-owner` is alive), but the glob matched every LIVE run's root too: the hooks
lane's land, begun at 21:37, failed with 47 failed and 1281 errors, FileNotFoundError across
unrelated tests. A run's root can only be its own if nothing sweeps the shared name. The Bash
guard (`lib/temp_sweep.py`) refuses `rm -r` and `find -delete/-exec rm` whose glob can reach a goh
temp root; a named root (`rm -rf "$TMPDIR/goh-pytest.ab12CD"`) is one run's and runs. The escape
is `# temp-sweep-ok: <reason>`.
"""

from __future__ import annotations

import pytest
from test_claude_load_guard import _decide


@pytest.mark.parametrize(
    "command",
    [
        'rm -rf "$TMPDIR"/goh-pytest.*/',  # the incident
        "rm -rf $TMPDIR/goh-pytest.*",
        "rm -fr /tmp/goh-*",
        "cd $TMPDIR && rm -r -f goh-pytest.*",
        "rm -rf ${TMPDIR:-/tmp}/*",
        "rm -rf /var/folders/ab/cd1234/T/goh-suite-cov-cache.*",
        "find $TMPDIR -maxdepth 1 -name 'goh-pytest.*' -exec rm -rf {} +",
        'find /tmp -name "goh-*" -delete',
        "rm --recursive --force $TMPDIR/goh-pytest.?????? ",
    ],
)
def test_a_glob_sweep_over_goh_temp_roots_is_refused(tmp_path, command):
    reason = _decide(command, tmp_path)
    assert reason is not None, command
    assert "goh-pytest" in reason and "temp-sweep-ok" in reason, reason


@pytest.mark.parametrize(
    "command",
    [
        'rm -rf "$TMPDIR/goh-pytest.ab12CD"',  # one named root: one run's own
        "rm -rf build/*.o",
        "rm -rf ~/scratch/item/*",
        "rm $TMPDIR/goh-pytest.*",  # not recursive: removes no root
        "echo 'rm -rf $TMPDIR/goh-pytest.*'",  # text, not code
        "find . -name '*.pyc' -delete",
        "rm -rf $TMPDIR/goh-pytest.* # temp-sweep-ok: the machine is idle, no gate runs",
    ],
)
def test_other_removals_run(tmp_path, command):
    assert _decide(command, tmp_path) is None, command
