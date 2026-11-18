"""The two sides a release canary compares (scripts/release_canary.py), split from it at its line
cap: the native goh a revision builds, that revision's gates, exported as a consumer gets them,
and how one side's run over a consumer reads (its Verdict, kept by lib/canary_memo.py)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "checks"))
from _gitutil import foreign_repo_env

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tui.lib import counted

BUILD_TIMEOUT_S = 1800

STEP_HEAD = re.compile(r"^· (.+) \(≤\d+s\)$")
STEP_OK = re.compile(r"^✓ (.+)$")
STEP_RED = re.compile(r"^✗ structural: (.+) failed \(")
FOUND = re.compile(r"^✗ \[")
# The house's words for a bound that fired (bounded_run.py, stepcmd.rs, _empty_scope_run.py).
TIMED_OUT = "TIMED OUT after"


def git_run(where: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(where), *args],
        env=foreign_repo_env(),
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def common_dir(repo: Path) -> str:
    r = git_run(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return str(Path(r.stdout.strip()).resolve()) if r.returncode == 0 else f"?{repo}"


def fresh(tree: Path) -> None:
    """The clone as committed again: a side's run leaves nothing the next side reads."""
    git_run(tree, "reset", "-q", "--hard")
    git_run(tree, "clean", "-q", "-fdx")


def resolve(goh: Path, rev: str) -> tuple[Path | None, str]:
    """(the native goh built from `rev`, why not): the gate's own resolver, which takes the build
    from the shared store or builds that commit into it (gates/_goh_bin.sh, build-goh.sh --rev)."""
    sha = git_run(goh, "rev-parse", "--verify", "-q", f"{rev}^{{commit}}").stdout.strip()
    if not sha:
        return None, f"{rev} is not a commit of {goh}"
    script = (
        '. "$1/gates/_goh_bin.sh" && _goh_resolve_uncached "$1/gates" "$2"'
        ' && printf "%s\\n%s" "$goh_native" "$goh_native_why"'
    )
    env = {k: v for k, v in foreign_repo_env().items() if not k.startswith("GOH_")}
    try:
        r = subprocess.run(
            ["bash", "-c", script, "_", str(goh), sha],
            env=env,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"resolving goh at {rev}: {e}"
    native, _, why = r.stdout.partition("\n")
    if native and os.access(native, os.X_OK):
        return Path(native), ""
    tail = (r.stderr.strip().splitlines() or [""])[-1]
    return None, f"no goh binary for {rev}: {why or tail or f'exit {r.returncode}'}"


def export(goh: Path, rev: str, dest: Path) -> str | None:
    """`git archive rev` into `dest` -- the gates the binary delegates to, as of that revision --
    shaped as a consumer's gate gets them (gates/_from_head.sh): `.goh-head` names the commit, so
    a gate script run from it runs in place and resolves that commit's binary. Without it a script
    there walked up for a checkout, found none, and judged differently (sys_updater's empty-scope
    probe read red here and green at its push)."""
    sha = git_run(goh, "rev-parse", "--verify", "-q", f"{rev}^{{commit}}").stdout.strip()
    if not sha:
        return f"{rev} is not a commit of {goh}"
    dest.mkdir(parents=True)
    # NOT `git archive | tar -x` (GOH.SUITEFLAKES). bsdtar exits at the archive's end marker
    # without draining the pipe, so git's last write of record padding, landing after that, takes
    # SIGPIPE: exit 141 with NOTHING on stderr, and `pipefail` made that the release canary's
    # "exporting v0.96.0: " with an empty reason (once, 2026-11-11). The archive is read whole,
    # then handed to tar, which may stop reading wherever it likes.
    archive = subprocess.run(
        ["git", "-C", str(goh), "archive", sha],
        env=foreign_repo_env(),
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if archive.returncode != 0:
        return f"exporting {rev}: {_failed('git archive', archive)}"
    unpack = subprocess.run(
        ["tar", "-x", "-C", str(dest)],
        input=archive.stdout,
        env=foreign_repo_env(),
        capture_output=True,
        check=False,
    )
    if unpack.returncode != 0:
        return f"exporting {rev}: {_failed('tar -x', unpack)}"
    (dest / ".goh-head").write_text(f"{sha}\n")
    return None


def _failed(what: str, r: subprocess.CompletedProcess) -> str:
    """A failed step, never an empty reason: its exit (a signal by name) and its stderr's tail."""
    why = r.stderr.decode("utf-8", "replace").strip() if isinstance(r.stderr, bytes) else r.stderr
    code = f"signal {-r.returncode}" if r.returncode < 0 else f"exit {r.returncode}"
    tail = " | ".join((why or "").strip().splitlines()[-3:])
    return f"{what} {code}: {tail or 'no stderr'}"


@dataclass
class Verdict:
    green: set[str] = field(default_factory=set)
    red: set[str] = field(default_factory=set)
    found: dict[str, list[str]] = field(default_factory=dict)
    # {step: its TIMED OUT line}: a step red ONLY because a bound fired gave no verdict.
    timed_out: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def _starved(block: list[str]) -> str | None:
    """A step's TIMED OUT line, when its output carries no `✗` finding of its own; else None."""
    lines = [ln.strip() for ln in block]
    if any(ln.startswith("✗") for ln in lines):
        return None
    return next((ln[:200] for ln in lines if TIMED_OUT in ln), None)


def parse(rc: int, out: str) -> Verdict:
    """One `goh structural --full --keep-going` run, step by step. An exit that is not 0 or 1, or
    one its output does not account for, is an ERROR -- never read as green.

    A red step whose output says TIMED OUT and carries no `✗` finding of its own was STARVED
    (GOH.CANARYLOAD: Adv360's empty-scope sweep at load ~80 read as "turned red -- fix the
    findings"): it lands in `timed_out`, never in `red`. A timeout beside a finding stays red."""
    v, step, block = Verdict(), None, []
    for line in out.splitlines():
        if m := STEP_HEAD.match(line):
            step, block = m.group(1), []
        elif m := STEP_RED.match(line):
            if why := _starved(block):
                v.timed_out[m.group(1)] = why
            else:
                v.red.add(m.group(1))
        elif (m := STEP_OK.match(line)) and m.group(1) != "all structural gates passed":
            v.green.add(m.group(1))
        elif FOUND.match(line) and step:
            v.found.setdefault(step, []).append(line[1:].strip())
        block.append(line)
    tail = " | ".join(out.strip().splitlines()[-3:])
    if rc not in (0, 1):
        v.error = f"exit {rc}: {tail}"
    elif rc == 1 and not v.red and not v.timed_out:
        v.error = f"exit 1 naming no failed step: {tail}"
    elif rc == 0 and (v.red or not v.green):
        v.error = f"exit 0 over {len(v.red)} red, {counted(len(v.green), 'green step')}: {tail}"
    return v


def kept(v: Verdict) -> dict | None:
    """A verdict as the memo keeps it; None for one that did not judge (an error, a starved run)."""
    if v.error or v.timed_out:
        return None
    d = asdict(v)
    return {**d, "green": sorted(v.green), "red": sorted(v.red)}


def restored(d: dict) -> Verdict:
    return Verdict(green=set(d.get("green", [])), red=set(d.get("red", [])),
                   found=dict(d.get("found", {})))  # fmt: skip
