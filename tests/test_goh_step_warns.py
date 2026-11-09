"""goh_step shows a PASSING step's `⚠` lines (GOH.STEPWARN).

A passing step's log is deleted unread, so its advisories -- the dependency-currency step's "a
lock skipped, NOT a clean bill" -- never reached the person reading the gate; crates-behind kept
a private `grep '⚠' "$GOH_LOG"` after its own step to compensate. The class is fixed in goh_step:
the `⚠` STATUS lines (first visible character, like xexit's `✗`) of a green step follow its ok
line, bounded by GOH_WARN_LINES with the count of what was cut. A `⚠` quoted mid-line is text.
"""

import subprocess
import textwrap
from pathlib import Path

from conftest import REPO_ROOT


def _run(tmp_path: Path, body: str, env_extra: str = "") -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""
        cd '{tmp_path}'
        {env_extra}
        . '{REPO_ROOT}/gates/_common.sh'
        goh_init outer
        {body}
    """)
    return subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_a_passing_steps_warn_lines_are_printed(tmp_path):
    r = _run(
        tmp_path,
        """goh_step "currency" /bin/bash -c "echo '⚠ lock skipped: NOT a clean bill'; echo plain"; goh_done""",
    )
    assert r.returncode == 0, r.stderr
    out = r.stdout + r.stderr
    assert "lock skipped: NOT a clean bill" in out
    assert "plain" not in out  # only the advisories, never the rest of the log


def test_the_warn_follows_the_ok_line_and_a_quoted_warn_is_text(tmp_path):
    r = _run(
        tmp_path,
        """goh_step "currency" /bin/bash -c "echo 'row: ⚠ quoted mid-line'; echo '  ⚠ indented advisory'"; goh_done""",
    )
    out = r.stdout + r.stderr
    assert "indented advisory" in out
    assert "quoted mid-line" not in out
    assert out.index("currency") < out.index("indented advisory")


def test_the_warn_lines_are_bounded_and_the_cut_is_counted(tmp_path):
    r = _run(
        tmp_path,
        """goh_step "noisy" /bin/bash -c 'for i in 1 2 3 4 5; do echo "⚠ advisory-$i"; done'; goh_done""",
        "export GOH_WARN_LINES=2",
    )
    out = r.stdout + r.stderr
    assert "advisory-1" in out and "advisory-2" in out
    assert "advisory-3" not in out
    assert "3 more" in out and "GOH_WARN_LINES" in out


def test_a_step_without_warnings_prints_nothing_extra(tmp_path):
    r = _run(tmp_path, """goh_step "quiet" /bin/bash -c "echo fine"; goh_done""")
    assert "⚠" not in r.stdout + r.stderr
    assert "more" not in r.stdout + r.stderr


def test_crates_behind_has_no_private_grep_for_the_warns():
    """The per-step workaround is gone: goh_step is the one place that surfaces them."""
    text = (REPO_ROOT / "gates" / "rust_gate.sh").read_text(encoding="utf-8")
    assert "grep '⚠' \"$GOH_LOG\"" not in text
