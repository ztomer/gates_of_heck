"""A tracked file that RUNS a backgrounded busy loop is refused, and the house way is named.

THE CLASS (2026-10-21). The house had a way to make load (`tools/load.py`, which registers its
spinners and kills exactly those), a backstop that kills REGISTERED orphans, and a Claude Code
guard that refused a Bash COMMAND spawning a backgrounded busy loop. And yet, at 13:00 on
2026-10-21, twenty-four orphaned spinners were burning four cores each --
`/bin/zsh /private/var/.../opencode/load.sh`, 78 minutes of CPU apiece, four and a half hours
old, parent 1, their owner long dead. The script was a hand-written FILE:

    for i in $(seq 1 24); do (while :; do :; done) & done

The guard could not see it (it judged commands, not files), and no gate looked at the files a
repository tracks, so the commit that added it was green.

So the house owns a check for the FILES now: `goh busy-loop`, a structural step in every repo.
The scope is the TRACKED set (the index at `--staged`) and among it the shell sources, and the
scope is PRINTED, so a scan that stopped reading its subject reads like a clean tree instead of
one. The escape is a reasoned per-line marker, `# load-ok: <reason>`; a marker with no reason
exempts nothing, and a marker nothing needs is a finding, so a stale escape cannot outlive the
defect it was written for.

TEXT IS NOT CODE. A loop inside a string, a heredoc body or a comment is text: `echo 'while :;
do :; done'` writes a loop, it does not run one. So the writer of a fixture that plants the form
is not refused by it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, commit_all, run_goh, stage, write

sys.path.insert(0, str(REPO_ROOT / "lib"))

import busyloop  # noqa: E402

SPINNER = "while :; do :; done &"
SLEEPER = "while true; do sleep 1; done &"


def _shell(repo: Path, body: str, name: str = "load.sh") -> Path:
    """A tracked shell script, and the repository that tracks it."""
    write(repo, name, f"#!/bin/sh\n{body}\n")
    commit_all(repo)
    return repo


def test_a_tracked_file_that_runs_a_backgrounded_busy_loop_is_refused(repo: Path) -> None:
    """THE CLASS. The file is the natural way to smuggle the loop in, and the file is refused."""
    _shell(repo, f"for i in $(seq 1 24); do ({SPINNER}) & done")
    result = run_goh(repo, "busy-loop")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "load.sh:" in result.stdout, result.stdout
    assert "tools/load.py" in result.stdout, result.stdout


def test_the_refusal_names_the_house_way_and_the_escape(repo: Path) -> None:
    """A refusal is a way forward, not a wall."""
    _shell(repo, SPINNER)
    result = run_goh(repo, "busy-loop")
    assert result.returncode == 1
    assert 'python3 "$GOH_DIR/tools/load.py"' in result.stdout, result.stdout
    assert "load-ok" in result.stdout, result.stdout


def test_a_clean_tree_is_green_and_prints_its_scope(repo: Path) -> None:
    """The scope is printed, so a scan that read nothing says so."""
    _shell(repo, "echo hello")
    result = run_goh(repo, "busy-loop")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tracked" in result.stdout, result.stdout
    assert "shell source(s)" in result.stdout, result.stdout


@pytest.mark.parametrize(
    "body",
    [
        # TEXT, not code: a loop inside a string.
        "echo 'while :; do :; done'",
        'echo "while :; do :; done &"',
        # TEXT: a heredoc body.
        "cat > other.sh <<'EOF'\nwhile :; do :; done &\nEOF",
        # TEXT: a comment.
        "# while :; do :; done &",
        # A loop that does work: it is not a spinner.
        SLEEPER,
        # A foreground busy loop hangs the shell that runs it, which is a different defect.
        "while :; do :; done",
    ],
)
def test_text_is_not_code(repo: Path, body: str) -> None:
    """`echo 'while :; do :; done'` writes a loop, it does not run one."""
    _shell(repo, body)
    result = run_goh(repo, "busy-loop")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_file_that_is_not_shell_is_not_judged(repo: Path) -> None:
    """The shape is a SHELL construct: a Python file that mentions it is not a spinner."""
    write(repo, "tool.py", f"SPIN = '{SPINNER}'\n")
    commit_all(repo)
    result = run_goh(repo, "busy-loop")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_escape_is_a_reasoned_marker(repo: Path) -> None:
    """A deliberate background busy loop says so, the way a deliberate discard does."""
    _shell(repo, f"# load-ok: measuring the queue on purpose\n{SPINNER}")
    result = run_goh(repo, "busy-loop")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_marker_with_no_reason_exempts_nothing(repo: Path) -> None:
    """`# load-ok:` alone is not a decision."""
    _shell(repo, f"# load-ok:\n{SPINNER}")
    result = run_goh(repo, "busy-loop")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "load-ok" in result.stdout, result.stdout


def test_a_stale_marker_is_a_finding(repo: Path) -> None:
    """A marker nothing needs is a marker that will outlive the defect it was written for."""
    _shell(repo, "# load-ok: the loop is gone\nsleep 1")
    result = run_goh(repo, "busy-loop")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "load-ok" in result.stdout, result.stdout


def test_the_index_is_judged_at_staged(repo: Path) -> None:
    """At `--staged` the INDEX is the subject: the commit's own content, not the worktree's."""
    write(repo, "load.sh", f"#!/bin/sh\n{SPINNER}\n")
    stage(repo, "load.sh")
    write(repo, "load.sh", "#!/bin/sh\necho clean\n")
    result = run_goh(repo, "busy-loop", "--staged")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "load.sh:" in result.stdout, result.stdout


def test_the_python_rule_and_the_rust_rule_agree(repo: Path) -> None:
    """ONE definition per language, and the two agree: the guard, the backstop and this check
    must judge the same file the same way, or the rule is three rules."""
    cases = [
        (f"#!/bin/sh\n{SPINNER}\n", True),
        ("#!/bin/sh\necho 'while :; do :; done'\n", False),
        ("#!/bin/sh\n# while :; do :; done &\n", False),
        ("#!/bin/sh\nwhile true; do sleep 1; done &\n", False),
        ("#!/bin/sh\nwhile :; do :; done\n", False),
    ]
    for body, spins in cases:
        write(repo, "load.sh", body)
        commit_all(repo)
        assert busyloop.spins(body) is spins, body
        result = run_goh(repo, "busy-loop")
        assert (result.returncode == 1) is spins, (body, result.stdout)
