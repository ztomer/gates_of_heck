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

A call that NAMES its repository passes `repo=` (never a hand-written `-C`): git ignores `-C` and
`cwd` once GIT_DIR is set, and a hook exports GIT_DIR and GIT_INDEX_FILE to everything it starts, so
`goh ship` started from a hook read the hook's repository and refused its own as "is not a git
checkout" (GOH.SHIPGITDIR). `repo=` drops git's OWN `--local-env-vars` list (checks/_gitutil.py) and
prepends `-C repo`; tests/test_git_deadline_class.py refuses a `-C` spelled by hand.

`run_reader` raises `subprocess.TimeoutExpired` like `subprocess.run` (a killed reader strands
nothing); `run_writer` returns None when it had to stop the writer, never a half result. The writer
runs in its own session so the TERM reaches its hooks too (`lib/killtree.py stop_group`).
"""

from __future__ import annotations

import functools
import importlib.util
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


@functools.lru_cache(maxsize=1)
def _local_env_vars() -> tuple[str, ...]:
    """git's own list of the variables that bind it to one repository (checks/_gitutil.py, loaded by
    path: the checks tier is copied whole into fixtures, so it is the one copy)."""
    path = Path(__file__).resolve().parent.parent / "checks" / "_gitutil.py"
    spec = importlib.util.spec_from_file_location("_goh_gitutil", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return tuple(mod.local_env_vars())


def repo_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """`base` (default: the process environment) without git's repository bindings."""
    drop = set(_local_env_vars())
    return {k: v for k, v in (os.environ if base is None else base).items() if k not in drop}


def _aimed(
    args: Sequence[str], repo: str | Path | None, env: Mapping[str, str] | None
) -> tuple[list[str], Mapping[str, str] | None]:
    if repo is None:
        return list(args), env
    return ["-C", str(repo), *args], repo_env(env)


def run_reader(
    args: Sequence[str],
    *,
    repo: str | Path | None = None,
    cwd: str | Path | None = None,
    timeout: float,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """`git <args>` for a command that only READS, captured, under `timeout` (TimeoutExpired).
    `repo` names the repository (no inherited GIT_DIR answers for it); `input_text` is fed on
    stdin (`cat-file --batch-check`); none means stdin is /dev/null."""
    args, env = _aimed(args, repo, env)
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
    repo: str | Path | None = None,
    cwd: str | Path | None = None,
    deadline_s: float,
    grace_s: float = killtree.TERM_GRACE_S,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """`git <args>` for a command that WRITES, in its own process group; None when stopped.

    Past `deadline_s` the whole group gets SIGTERM (git's handler removes its lock files, its hooks
    die with it) and SIGKILL only if it is still there after `grace_s`. `repo` as in `run_reader`.
    """
    args, env = _aimed(args, repo, env)
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


__all__ = ["OPTIONAL_LOCKS", "reader_env", "repo_env", "run_reader", "run_writer"]
