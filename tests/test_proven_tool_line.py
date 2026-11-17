"""A tool's version line in every proven key is its STDOUT, never its stderr (GOH.PREPARITY).

`_proven_tool` keyed `cargo -V` and `rustc -V` by the first line of `2>&1`: a rustup proxy that
says something on stderr first (a lock it waits on, a channel it syncs -- what a loaded box
makes it do) put that line in the key instead of the version, so the same tree keyed apart and a
proven step ran again (the load-flaky second runs of tests/test_rust_group_phases.py and
tests/test_rust_gate_scoped_cache.py). A tool that prints its version only on stderr still keys
by it.
"""

from __future__ import annotations

import subprocess

import pytest
from conftest import REPO_ROOT, hermetic_env


def _line(cmd: str) -> str:
    script = f". \"{REPO_ROOT}/gates/_proven.sh\" >/dev/null 2>&1; _proven_tool sh bash -c '{cmd}'"
    env = hermetic_env(drop_git=True)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL, env=env).stdout  # fmt: skip


@pytest.mark.parametrize("noise", ["echo noise $RANDOM$$ >&2; ", ""])
def test_the_version_line_is_stdout_whatever_the_tool_says_on_stderr(noise: str) -> None:
    lines = {_line(f"{noise}echo tool-1.2.3") for _ in range(3)}
    assert lines == {"tool sh tool-1.2.3\n"}, lines


def test_a_tool_that_says_its_version_only_on_stderr_keys_by_it() -> None:
    assert _line("echo tool-9 >&2") == "tool sh tool-9\n"
