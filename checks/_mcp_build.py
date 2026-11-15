"""_mcp_build.py -- the build a `cargo run` MCP server implies, run BEFORE its wire bound starts.

checks/check_mcp_wire.py times each server from its start to its last tools/list page. A server
named as `cargo run` builds inside that bound, so the build was timed as the answer: token-pool
751531b's push failed "no answer to server/discover within 300s" at load ~120 (GOH.MCPWIRELOAD).
Here the build is derived, run under its own bound, and said as `build:` when it fails.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import foreign_repo_env

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.killtree import session_pgid, stop_group

DEFAULT_BUILD_TIMEOUT = 1800
# `cargo [+toolchain] run` as its own words: `mycargo run` or `cargo-run` is some other program.
_CARGO_RUN = re.compile(r"(?<![\w./-])(cargo(?:\s+\+[\w.-]+)?\s+)run(?=\s|$)")
_ARGS_END = re.compile(r"(?:^|\s)--(?:\s|$)")
_SHELL_OPS = re.compile(r"[;&|<>`]|\$\(")


def build_command(command: str) -> str | None:
    """The build a server's start command implies, or None when it names none the gate can see.

    Only ONE `cargo [+toolchain] run` is read: the same words with `run` made `build`, everything
    from the server's `--` dropped, and everything before `cargo` (an env prefix, a `cd`) kept, so
    the build sees the environment the run will and the run then finds it fresh. A shell operator
    among cargo's own arguments, or a second `cargo run`, is a command the gate does not take apart:
    it runs as written, its build inside its bound, as before."""
    found = list(_CARGO_RUN.finditer(command))
    if len(found) != 1:
        return None
    m = found[0]
    rest = command[m.end() :]
    cut = _ARGS_END.search(rest)
    args = rest[: cut.start()] if cut else rest
    if _SHELL_OPS.search(args):
        return None
    return command[: m.start()] + m.group(1) + "build" + args.rstrip()


def server_env(scratch_home: str) -> dict[str, str]:
    """The environment a server, and its build, run in: GOH_MCP_SCRATCH, and the owner's Rust
    toolchain pinned by its own variables, so a server that moves HOME does not move the
    toolchain."""
    env = foreign_repo_env()
    env["GOH_MCP_SCRATCH"] = scratch_home
    home = os.path.expanduser("~")
    for key, rel in (("CARGO_HOME", ".cargo"), ("RUSTUP_HOME", ".rustup")):
        if not env.get(key) and os.path.isdir(os.path.join(home, rel)):
            env[key] = os.path.join(home, rel)
    return env


def build(command: str, root: pathlib.Path, timeout: int) -> str | None:
    """Run one derived build under its own bound, in its own session and scratch; None when it
    built, else the `build:` finding (its status or its bound, and its output's tail)."""
    scratch = tempfile.mkdtemp(prefix="goh-mcp-build.")
    try:
        os.mkdir(os.path.join(scratch, "home"))
        with open(os.path.join(scratch, ".out"), "w+b") as out:
            proc = subprocess.Popen(
                ["/bin/sh", "-c", command],
                cwd=root,
                env=server_env(os.path.join(scratch, "home")),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                code = proc.wait(timeout=timeout)
                verdict = None if code == 0 else f"failed (status {code})"
            except subprocess.TimeoutExpired:
                verdict = f"did not finish within {timeout}s"
            stop_group(session_pgid(proc), proc=proc)  # pass or fail: nothing outlives the build
            proc.wait()
            if verdict is None:
                return None
            out.seek(0)
            tail = out.read().decode("utf-8", "replace").splitlines()[-12:]
        return f"build: {command} {verdict}" + "".join(f"\n      {s}" for s in tail)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
