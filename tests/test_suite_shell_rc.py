"""No test child sources the caller's shell rc (GOH.ZSHSPLITGUARD, 2026-11-21).

`BASH_ENV=~/.bashrc` in the agent's environment made every bash a test spawned rebuild its env from
a `zsh -lic` under the suite's throwaway home and export that PATH over the test's: the suite would
not start (`cargo: command not found` in tests/_drift_guard.py's prebuild). suite_home drops it.
"""

from __future__ import annotations

import os
import subprocess

import suite_home
from conftest import hermetic_env
from timing_bounds import patience


def test_the_rc_variables_are_dropped_from_a_caller_environment() -> None:
    environ = {"BASH_ENV": "/h/.bashrc", "ENV": "/h/.shrc", "PATH": "/p"}
    suite_home.drop_shell_rc(environ)
    assert environ == {"PATH": "/p"}


def test_the_suite_and_its_children_carry_no_shell_rc() -> None:
    assert not {"BASH_ENV", "ENV"} & set(os.environ)
    out = subprocess.run(
        ["bash", "-c", 'printf "%s|%s" "${BASH_ENV-unset}" "$PATH"'], capture_output=True,
        text=True, timeout=patience(30), env=hermetic_env(PATH="/usr/bin:/bin"),
        stdin=subprocess.DEVNULL, check=True,
    )  # fmt: skip
    assert out.stdout == "unset|/usr/bin:/bin"
