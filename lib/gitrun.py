"""git under a deadline, without stranding a lock (GOH.GITKILL, 2026-11-08).

THE CLASS, found in .loop (`4e82af6`, `7ae11d8`). `subprocess.run(["git", ...], timeout=T)` SIGKILLs
git alone at the deadline:

* a WRITER (commit, add, merge, checkout) dies holding `.git/index.lock` and orphans its hook, so
  every later commit in the repository is refused until someone deletes the file;
* a READER (`git status`, `git diff`) takes the same lock OPTIONALLY, to refresh the index it just
  read, and strands it the same way.

TWO SEAMS, so the wrong state is unrepresentable (tests/test_git_deadline_class.py scans for any
other spelling):

    run_reader(["status", "--short"], cwd=repo, timeout=5)   # GIT_OPTIONAL_LOCKS=0: no lock to strand
    run_writer(["commit", "-q", "-m", m], cwd=repo, deadline_s=60)   # own group; TERM, a grace, KILL

`run_reader` raises `subprocess.TimeoutExpired` like `subprocess.run` (a killed reader strands
nothing); `run_writer` returns None when it had to stop the writer, never a half result. The writer
runs in its own session so the TERM reaches its hooks too (`lib/killtree.py stop_group`).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import killtree

OPTIONAL_LOCKS = "GIT_OPTIONAL_LOCKS"


def reader_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """`base` (default: the process environment) with git told to take no optional lock."""
    env = dict(os.environ if base is None else base)
    env[OPTIONAL_LOCKS] = "0"
    return env


def run_reader(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    timeout: float,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """`git <args>` for a command that only READS, captured, under `timeout` (TimeoutExpired).
    `input_text` is fed on stdin (`cat-file --batch-check`); none means stdin is /dev/null."""
    if input_text is not None:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=reader_env(env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            input=input_text,
        )
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=reader_env(env),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def run_writer(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    deadline_s: float,
    grace_s: float = killtree.TERM_GRACE_S,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """`git <args>` for a command that WRITES, in its own process group; None when stopped.

    Past `deadline_s` the whole group gets SIGTERM (git's handler removes its lock files, its hooks
    die with it) and SIGKILL only if it is still there after `grace_s`.
    """
    proc = subprocess.Popen(
        ["git", *args],
        cwd=cwd,
        env=dict(os.environ if env is None else env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=max(0.1, deadline_s))
    except subprocess.TimeoutExpired:
        killtree.stop_group(proc.pid, grace_s, proc)
        try:
            proc.communicate(timeout=5)  # drain: a grandchild holding the pipe must not block us
        except subprocess.TimeoutExpired:
            pass
        return None
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


__all__ = ["OPTIONAL_LOCKS", "reader_env", "run_reader", "run_writer"]
