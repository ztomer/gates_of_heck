"""goh_init's EXIT trap must preserve the script's exit status and clean up
every log it created.

Class: "a cleanup trap that fails open". The old trap was
`trap "rm -f '$GOH_LOG'" EXIT` — double-quoted, so $GOH_LOG expanded at SET
time, and the trap's exit status was `rm`'s (0). A gate that died abnormally
(a syntax error mid-file) therefore exited 0: proven end-to-end as a silent
green commit of a broken structural.sh. The same set-time expansion orphaned
the first mktemp log when goh_init ran twice.

Red proofs (both red against pre-fix _common.sh):
  - appended syntax error after goh_init exited 0 (the whole finding)
  - double goh_init left the first log behind in TMPDIR
"""

import subprocess
from pathlib import Path

from conftest import REPO_ROOT

BASH = "/bin/bash"


def _run(script_body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, "-c", script_body], capture_output=True, text=True
    )


def test_syntax_error_after_goh_init_exits_nonzero(tmp_path):
    """THE regression: abnormal termination must not be laundered to 0."""
    r = _run(
        f"export TMPDIR='{tmp_path}'\n"
        f". '{REPO_ROOT}/gates/_common.sh'\n"
        "goh_init demo\n"
        "ok starting\n"
        "if [;broken]\n"  # syntax error: bash aborts here
    )
    assert r.returncode != 0, (
        f"syntax error after goh_init exited {r.returncode} — "
        f"the EXIT trap is failing open: stderr={r.stderr!r}"
    )


def test_double_goh_init_cleans_every_log(tmp_path):
    """A second goh_init must not orphan the first mktemp log.

    Log paths are echoed to a file from INSIDE the script: macOS mktemp -t
    ignores TMPDIR (confstr _CS_DARWIN_USER_TEMP_DIR), so a glob in a
    redirected TMPDIR would silently check an empty directory.
    """
    out = tmp_path / "logs.txt"
    r = _run(
        f". '{REPO_ROOT}/gates/_common.sh'\n"
        "goh_init one\n"
        "log1=\"$GOH_LOG\"\n"
        "goh_init two\n"
        f"printf '%s\\n%s\\n' \"$log1\" \"$GOH_LOG\" > '{out}'\n"
        "exit 7\n"
    )
    assert r.returncode == 7, r.stderr
    logs = [Path(x) for x in out.read_text().splitlines()]
    assert len(logs) == 2, out.read_text()
    leftovers = [x for x in logs if x.exists()]
    assert leftovers == [], f"double goh_init orphaned logs: {leftovers}"


def test_normal_failure_rc_survives_the_trap(tmp_path):
    """die()'s exit status must reach the caller through the trap."""
    r = _run(
        f"export TMPDIR='{tmp_path}'\n"
        f". '{REPO_ROOT}/gates/_common.sh'\n"
        "goh_init demo\n"
        "die boom\n"
    )
    assert r.returncode == 1, r.stderr


def test_exit_zero_without_goh_done_is_a_failure(tmp_path):
    """bash 3.2 + set -e hands $?=0 to an EXIT trap even after a parse
    error, so rc-preservation alone still fails open. Exit 0 is therefore
    earned ONLY by running goh_done (the completion sentinel)."""
    r = _run(
        f"export TMPDIR='{tmp_path}'\n"
        f". '{REPO_ROOT}/gates/_common.sh'\n"
        "goh_init demo\n"
        "ok starting\n"
        "if [;broken]\n"  # parse error: bash 3.2 delivers rc=0 to the trap
    )
    assert r.returncode != 0
    assert "without completing" in r.stderr

    # And a gate that DID run goh_done keeps its honest 0.
    ok_case = _run(
        f". '{REPO_ROOT}/gates/_common.sh'\n"
        "goh_init demo\n"
        "true\n"
        "goh_done\n"
    )
    assert ok_case.returncode == 0, ok_case.stderr
