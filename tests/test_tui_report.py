"""E59: a report is ONE write, and the house TUI's two halves agree on it.

THE FINDING (2026-10-21, closing E39). The gate-slot wait report was TWO writes: the
header, then the line that names each holder. A reader that polls the output and waits for
the header could read the file BETWEEN the writes and treat a half-written report as
final -- the window was 56 us, and a reader polling every millisecond landed in it (1 of
20 trials against the real writer; 20 of 20 against the instrumented one).

No test-side rule can prevent that: the soundness of a wait depends on the writer's write
ORDER, which a reader cannot know. So the fix is on the writer's side -- a report that is
written ONCE, where the intermediate state does not exist at all. That is a PRIMITIVE in
the house TUI, and the TUI has TWO halves (`tui/lib.py` and `tui/lib.sh`) which have
drifted before, so the primitive lands in both and the two are pinned to agree here.

WHAT IS PINNED.

  1. the two halves render the SAME BYTES for the same report -- the drift that made
     `tui/lib.py` miss `step` for a year (2026-10-03) is the drift this pins;
  2. the PYTHON half writes the report in ONE write(2) -- the text layer would split it at
     every newline on a terminal, so the bytes go to the byte layer;
  3. the SHELL half issues ONE printf with the whole report -- the same guarantee, in the
     language the other half is written in.

Both halves are run against a COPY of tui/: a copy with no gates/ beside it does not
re-exec HEAD's copy (contract C4), so the WORKING TREE's bytes are what is measured.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT

HEADER = "all 1 gate slots are held -- waiting for one (GOH_GATE_SLOTS=1, /tmp/slots):"
DETAILS = (
    "slot-0: zinc (pid 4213, since 2026-10-21 05:07:12, 3s)",
    "slot-1: games (pid 4214, since 2026-10-21 05:07:13, 2s)",
)
EXPECTED = (
    "→ all 1 gate slots are held -- waiting for one (GOH_GATE_SLOTS=1, /tmp/slots):\n"
    "· slot-0: zinc (pid 4213, since 2026-10-21 05:07:12, 3s)\n"
    "· slot-1: games (pid 4214, since 2026-10-21 05:07:13, 2s)\n"
)


def _sandbox(tmp_path: Path) -> Path:
    """A copy of tui/ with no gates/ beside it: the C4 re-exec does not fire, so the copy's
    own lib.sh is the one that runs -- the working tree's, not HEAD's.

    The copy sits in a TREE (tmp/tree/tui), not directly in tmp_path: the python half is run
    with that tree as its cwd, and `import tui.lib` must find THIS copy -- the house exports
    PYTHONPATH (the shared checkout), which would otherwise answer for it.
    """
    tree = tmp_path / "tree"
    tree.mkdir()
    shutil.copytree(REPO_ROOT / "tui", tree / "tui", ignore=shutil.ignore_patterns("__pycache__"))
    return tree


def _env(tree: Path) -> dict[str, str]:
    import os

    return dict(
        os.environ,
        NO_COLOR="1",  # the plain shape, so the two halves are compared as bytes
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(tree),  # this tree's tui, not the shared checkout's
    )


def _python_report(tree: Path, *details: str) -> bytes:
    return _python_run(
        tree, "import sys; from tui.lib import report; report(*sys.argv[1:])\n", HEADER, *details
    )


def _python_run(tree: Path, code: str, *args: str) -> bytes:
    r = subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=tree,
        env=_env(tree),
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return r.stdout


def _shell_report(tree: Path, *details: str) -> bytes:
    return _shell_run(tree, '. "$0/tui/lib.sh"; report "$@"\n', HEADER, *details)


def _shell_run(tree: Path, body: str, *args: str) -> bytes:
    script = f'. "{tree / "tui" / "lib.sh"}"\n' + body
    r = subprocess.run(
        ["bash", "-c", script, "bash", *args],
        cwd=tree,
        env=_env(tree),
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return r.stdout


def test_the_two_halves_of_the_tui_render_the_same_report(tmp_path):
    """The drift pin: `tui/lib.py` and `tui/lib.sh` are two halves of ONE library."""
    tree = _sandbox(tmp_path)
    assert _python_report(tree, *DETAILS) == _shell_report(tree, *DETAILS)


def test_the_report_is_the_header_and_the_details_in_the_house_shape(tmp_path):
    tree = _sandbox(tmp_path)
    assert _python_report(tree, *DETAILS).decode("utf-8") == EXPECTED
    assert _shell_report(tree, *DETAILS).decode("utf-8") == EXPECTED


def test_the_report_renders_exactly_what_the_two_writes_it_replaces_rendered(tmp_path):
    """The two writes were `info(header)` and `step(detail)` each; the report is those same
    bytes, in one write. A writer converted to `report` therefore prints what it printed."""
    tree = _sandbox(tmp_path)
    py_two = _python_run(
        tree,
        "from tui.lib import info, step; import sys\n"
        "h, ds = sys.argv[1], sys.argv[2:]\n"
        "info(h)\n"
        "[step(d) for d in ds]\n",
        HEADER,
        *DETAILS,
    )
    py_one = _python_run(
        tree,
        "from tui.lib import report; import sys; report(*sys.argv[1:])\n",
        HEADER,
        *DETAILS,
    )
    assert py_one == py_two
    sh_two = _shell_run(
        tree,
        'info "$1"; shift; for d in "$@"; do step "$d"; done\n',
        HEADER,
        *DETAILS,
    )
    sh_one = _shell_run(tree, 'report "$@"\n', HEADER, *DETAILS)
    assert sh_one == sh_two


def test_a_detail_stays_data_in_both_halves(tmp_path):
    """A label with a percent sign, a backslash or a newline is DATA -- never a format."""
    tree = _sandbox(tmp_path)
    detail = "100%s \\n not-a-newline"
    assert _python_report(tree, detail) == _shell_report(tree, detail)
    assert "100%s \\n not-a-newline" in _python_report(tree, detail).decode("utf-8")


def test_the_python_half_writes_the_report_once():
    """ONE write(2): the text layer splits a multi-line report at every newline on a
    terminal, so the bytes go to the byte layer. A reader can then only ever see the whole
    report or none of it."""
    sys.path.insert(0, str(REPO_ROOT))
    import tui.lib as tui

    class Recorder:
        def __init__(self) -> None:
            self.writes: list[bytes] = []
            self.buffer = self

        def write(self, data):  # the byte layer
            self.writes.append(data)
            return len(data)

        def flush(self) -> None:
            pass

        def isatty(self) -> bool:
            return False

    rec = Recorder()
    real = sys.stdout
    sys.stdout = rec  # type: ignore[assignment]
    try:
        tui.report(HEADER, *DETAILS)
    finally:
        sys.stdout = real
    assert len(rec.writes) == 1, f"the report was written in {len(rec.writes)} writes"
    text = rec.writes[0].decode("utf-8")
    assert HEADER in text and all(d in text for d in DETAILS), text


def test_the_shell_half_issues_one_printf(tmp_path):
    """ONE printf, with the whole report in it -- the same guarantee, in the other language."""
    tree = _sandbox(tmp_path)
    log = tmp_path / "printf.log"
    script = (
        f'. "{tree / "tui" / "lib.sh"}"\n'
        'printf() { builtin printf \'CALL %s\\n\' "$#" >> "$GOH_TEST_LOG"; builtin printf "$@"; }\n'
        'report "$@"\n'
    )
    r = subprocess.run(
        ["bash", "-c", script, "bash", HEADER, *DETAILS],
        cwd=tree,
        env=dict(_env(tree), GOH_TEST_LOG=str(log)),
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    calls = log.read_text().splitlines()
    assert len(calls) == 1, f"the report was printed in {len(calls)} calls: {calls}"
    assert calls[0] == "CALL 2", calls  # the format and the one argument that carries it
    assert r.stdout.decode("utf-8") == EXPECTED
