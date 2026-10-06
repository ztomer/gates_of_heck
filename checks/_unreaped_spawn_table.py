"""The MEASURED TABLE, as data. One row per shape; `checks/_unreaped_spawn_probe.py` runs them.

Split out of the probe at the 500-line cap, and the cut is the obvious one: this file is a list of
shapes and the other is a driver, with no shared state. The rows ARE the specification -- R2 says a
checker's spec is a measured table, not a docstring -- so they are kept where adding a shape is a
one-line edit and cannot accidentally disturb the code that runs them.
"""

from _unreaped_spawn_table_rust import RUST  # noqa: E402

PYTHON = [
    (
        "Popen with no wait and no context manager",
        """
import subprocess
def test_x():
    proc = subprocess.Popen(["x"])
    assert proc is not None
""",
        True,
    ),
    (
        "Popen with the reap below a raising line",
        """
import subprocess
def test_x():
    proc = subprocess.Popen(["x"])
    assert proc is not None
    proc.wait()
""",
        True,
    ),
    (
        "Popen under `with`",
        """
import subprocess
def test_x():
    with subprocess.Popen(["x"]) as proc:
        assert proc is not None
""",
        False,
    ),
    (
        "Popen then wait() immediately",
        """
import subprocess
def test_x():
    proc = subprocess.Popen(["x"])
    assert proc.wait() == 0
""",
        False,
    ),
    (
        "subprocess.run waits internally, so it cannot leak",
        """
import subprocess
def test_x():
    assert subprocess.run(["x"]).returncode == 0
""",
        False,
    ),
    (
        "the shape inside a fixture string is not code",
        """
import subprocess
FIXTURE = "proc = subprocess.Popen([\\"x\\"])"
def test_x():
    assert FIXTURE
""",
        False,
    ),
]

SHELL = [
    (
        "a backgrounded command with no kill, wait or trap",
        '#!/usr/bin/env bash\n"$BIN" --serve &\necho go\n',
        True,
    ),
    (
        "a trap whose cleanup kills the recorded pid",
        '#!/usr/bin/env bash\npid="\ncleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; }\n'
        'trap cleanup EXIT\n"$BIN" --serve &\npid=$!\n',
        False,
    ),
    (
        "a wait() on the recorded pid",
        '#!/usr/bin/env bash\n"$BIN" --serve &\npid=$!\nwait "$pid"\n',
        False,
    ),
    (
        "`&&` is not a background launch",
        "#!/usr/bin/env bash\n[ -x x ] && echo yes\n",
        False,
    ),
    (
        "a `&` inside quotes is data, and a heredoc body is data",
        '#!/usr/bin/env bash\nnote="run it & wait"\ncat <<EOF\nfoo &\nEOF\n',
        False,
    ),
    # A QUOTED tag (`<<'EOF'`, the form that stops expansion and the commonest in test scripts)
    # was blanked with the quotes before the tag was read, so its body was scanned as code and a
    # `&` in it was a "background launch" (found porting the masker, Phase N1).
    (
        "a single-quoted heredoc tag's body is data",
        "#!/usr/bin/env bash\ncat <<'EOF'\nfoo &\nEOF\n",
        False,
    ),
    (
        "a double-quoted, dash heredoc tag's body is data",
        '#!/usr/bin/env bash\ncat <<-"EOF"\n\tfoo &\n\tEOF\n',
        False,
    ),
    (
        "a here-string is not a heredoc, so the next line is still code",
        "#!/usr/bin/env bash\ncat <<<EOF\nserver &\n",
        True,
    ),
]


# The shapes the ESTATE SWEEP found, added to the table on 2026-10-03 because the sweep is how they
# were found: four of the first seven hits across 12 repos were CORRECT code, and a checker that
# flags the fix is a checker that gets switched off.
PYTHON_EXTRA = [
    (
        "try/finally whose finally reaps is a guard, not a leak",
        """
import subprocess
def test_x():
    proc = subprocess.Popen(["x"])
    try:
        assert proc.pid > 0
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait()
""",
        False,
    ),
    (
        "a finally that restores a FILE but not the process, with asserts above the reap",
        """
import subprocess
def test_x():
    proc = subprocess.Popen(["x"])
    try:
        assert proc.pid > 0
        out = proc.communicate(timeout=120)
    finally:
        SCRIPT.write_bytes(original)
""",
        True,
    ),
    (
        "the Popen stored on self: a handoff, not a leak",
        """
import subprocess
class DemoServer:
    def __init__(self):
        self.proc = subprocess.Popen(["x"])
""",
        False,
    ),
    (
        "the Popen returned to the caller: a handoff",
        """
import subprocess
def _spawn():
    return subprocess.Popen(["x"])
""",
        False,
    ),
]
