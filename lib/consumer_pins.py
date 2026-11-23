"""THE MANIFEST of the pins goh owns in a consumer; how a goh release lands them (GOH.PINPROPAGATE,
GOH.PINPUSHGATE).

A consumer carries copies of versions goh decides. On 2026-11-15 two of them stranded the estate:

  * ruff. goh judges with exactly one ruff (gates/required_tools.tsv, read by gates/ruff_house.py)
    and refuses any other from a repo's venv; four consumers' pyproject.toml + uv.lock still said
    `ruff==0.17.4` after goh moved to 0.17.5, so every commit there was refused until a hand bump
    (TP.RUFFPIN, ZN.RUFF0174, GAF.RUFF0174 were three more at 0.17.4).
  * the wf pin. `goh wf-pin --bump` committed "ci: pin goh vX" in every consumer and pushed none:
    after v0.121.0 about 17 repos sat ahead of origin, and the next writer inherited a rebase.

So a pin goh owns is never hand-bumped, and a pin commit is never left behind. PINS below is the one
list; each row says where its version is WRITTEN (never here: a version written twice is the class).
`goh wf-pin --bump`, which goh ship's install step runs after each goh release, moves every stale
pin of every row in one commit per consumer through that repo's own hooks (lib/estate_commit.py)
and records each outcome in the ship's record (`record_path`), which goh ship carries into its
result as `consumer_pins`. The PUSHES are not the ship's (GOH.PINPUSHGATE, ruling 2026-11-15): each
runs that consumer's FULL push gate (v0.124.0: 2010 s of the install's 2037 s), so lib/pin_sweep.py
pushes every pin commit ahead of its upstream -- this one's and any an earlier bump left -- in a
detached, bounded sweep after the ship returns, and records each outcome where `goh ship --status`
and the next `goh wf-pin` listing read it. An owner's unpushed commit is never pushed by goh: the
pin commit then waits on it, named.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import bounded_run  # lib/: every importer (scripts/wf_pin.py, tools/ship.py) puts it on sys.path
import estate_commit
import gate_slot
import land_lock
import lock_order
import step_timings
from tmp_root import goh_tmp  # every temp root under $TMPDIR/goh.noindex (GOH.TMPNOINDEX)

from tui.lib import counted

GOH = Path(__file__).resolve().parent.parent

LOAD = GOH / "tools" / "load.py"  # each push gate is admitted by it, one slot per gate
TIMEOUT = 1800  # one consumer's push gate at most
# The whole sweep's pushes, under goh ship's step bound (GOH_SHIP_TIMEOUT, 3600 s): a sweep killed
# by that bound records nothing, so it stops pushing first and names what it left for the next one.
BUDGET = 2700
_deadline = [time.monotonic() + BUDGET]


def budget(seconds: float) -> None:
    _deadline[0] = time.monotonic() + seconds


@dataclass(frozen=True)
class Kind:
    name: str
    what: str  # the copies in a consumer
    source: str  # where goh writes the version; nothing else does


PINS: tuple[Kind, ...] = (
    Kind("goh", "the goh release a hosted gate checks out: a tracked .github/workflows/*.yml "
         "(`ref:` beside `repository: OWNER/gates_of_heck`, `GOH_TAG:`, `git clone --branch`)",
         "goh's newest vX.Y.Z tag (goh ship passes the one it just pushed)"),
    Kind("ruff", "`ruff==X.Y.Z` in the root pyproject.toml, a root requirements*.txt or a tracked "
         "workflow's install; the uv.lock beside the pyproject relocked, its .venv given that ruff",
         "gates/required_tools.tsv, read by gates/ruff_house.py RUFF"),
)  # fmt: skip


@dataclass(frozen=True)
class Pin:
    line: int  # 1-based
    start: int  # the version's span within the line
    end: int
    tag: str


def rewrite(text: str, moves: list[tuple[Pin, str]]) -> str:
    """TEXT with each pin's span replaced by its new version, right to left within a line, so two
    pins on one line (a clone's goh tag and its ruff) never shift each other."""
    lines = text.splitlines(keepends=True)
    for p, new in sorted(moves, key=lambda m: (m[0].line, m[0].start), reverse=True):
        ln = lines[p.line - 1]
        lines[p.line - 1] = ln[: p.start] + new + ln[p.end :]
    return "".join(lines)


def bumped(text: str, pins: list[Pin], tag: str) -> str:
    return rewrite(text, [(p, tag) for p in pins])


def ruff_pin() -> str:
    """goh's ruff, from its one writer (gates/ruff_house.py RUFF)."""
    sys.path.insert(0, str(GOH / "gates"))
    try:
        import ruff_house
    finally:
        sys.path.pop(0)
    return ruff_house.RUFF


_RUFF = re.compile(r"(?<![\w.-])ruff==(\d+(?:\.\d+)+)\b")


def scan_ruff(text: str) -> list[Pin]:
    """Every `ruff==X.Y.Z` in code; after a `#` it is prose and never rewritten."""
    pins = []
    for i, line in enumerate(text.splitlines()):
        code = line.split("#", 1)[0]
        pins += [Pin(i + 1, m.start(1), m.end(1), m.group(1)) for m in _RUFF.finditer(code)]
    return pins


def ruff_files(repo: Path, workflows: list[str]) -> list[str]:
    out = estate_commit.git(repo, "ls-files", "-z", "--", "pyproject.toml", "requirements*.txt")
    return sorted({f for f in out.stdout.split("\0") if f and "/" not in f} | set(workflows))


def stale_ruff(repo: Path, workflows: list[str], want: str) -> dict[str, list[Pin]]:
    old: dict[str, list[Pin]] = {}
    for rel in ruff_files(repo, workflows):
        try:
            text = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if behind := [p for p in scan_ruff(text) if p.tag != want]:
            old[rel] = behind
    return old


def relock(repo: Path, files: list[str], want: str) -> tuple[list[str], Callable[[], str | None]]:
    """(the paths a ruff move also owns, the step that moves them): the uv.lock beside a moved
    pyproject.toml, relocked by uv; and the repo's .venv given that ruff, so the commit gate that
    judges the bump runs goh's ruff (the venv is machine state, never committed)."""
    lock = "pyproject.toml" in files and estate_commit.git(
        repo, "ls-files", "--error-unmatch", "--", "uv.lock").returncode == 0  # fmt: skip
    venv = (repo / ".venv" / "bin" / "python").is_file() and any(
        not f.startswith(".github/") for f in files)  # fmt: skip

    def run() -> str | None:
        steps = ([["uv", "lock"]] if lock else []) + (
            [["uv", "pip", "install", "--python", ".venv/bin/python", f"ruff=={want}"]] if venv
            else [])  # fmt: skip
        for argv in steps:
            try:
                done = subprocess.run(argv, cwd=repo, capture_output=True, text=True, check=False,
                                      timeout=600, env=estate_commit.env(),
                                      stdin=subprocess.DEVNULL)  # fmt: skip
            except (OSError, subprocess.TimeoutExpired) as e:
                return f"`{' '.join(argv)}` did not run: {e}"
            if done.returncode != 0:
                said = " | ".join((done.stdout + done.stderr).strip().splitlines()[-2:])
                return f"`{' '.join(argv)}` failed ({said})"
        return None

    return (["uv.lock"] if lock else []), run


# Every pin commit goh writes says this first; one that touches only pin files is goh's own.
SUBJECT = re.compile(r"^ci: pin goh v\d+\.\d+\.\d+ ")


def _pin_file(rel: str) -> bool:
    return rel.startswith(".github/workflows/") or rel in ("pyproject.toml", "uv.lock") or (
        "/" not in rel and rel.startswith("requirements") and rel.endswith(".txt"))  # fmt: skip


def _commits(repo: Path, span: str) -> list[tuple[str, str]]:
    out = estate_commit.git(repo, "log", "--format=%H%x09%s", span).stdout
    return [(sha, subject) for sha, _, subject in (ln.partition("\t") for ln in out.splitlines())]


# A hook refresh's commit (scripts/hooks_refresh.py MESSAGE) is goh's own as well, touching only
# `.githooks` (GOH.HOOKREFRESHPUSH): `goh hooks --refresh` committed and pushed none, and on
# 2026-11-15 six consumers sat ahead of origin with nothing but hook commits until a hand sweep.
# It is pushed by the same door, so a refresh and a pin sweep each carry the other's commits.
HOOKS_SUBJECT = re.compile(r"^chore: refresh goh's stock hooks \(")


def is_pin_commit(repo: Path, sha: str, subject: str) -> bool:
    """SHA is goh's own commit: a pin commit touching only pin files, or a hook refresh touching
    only `.githooks`. Only these are ever pushed by goh."""
    files = estate_commit.git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha)
    names = [f for f in files.stdout.splitlines() if f]
    if HOOKS_SUBJECT.match(subject):
        return bool(names) and all(n.startswith(".githooks/") for n in names)
    return bool(SUBJECT.match(subject) and names and all(map(_pin_file, names)))


def upstream(repo: Path) -> tuple[str, str] | None:
    """(remote, branch) the checked-out default branch pushes to, or None."""
    branch = estate_commit.git(repo, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
    remote = estate_commit.git(repo, "config", f"branch.{branch}.remote").stdout.strip()
    merge = estate_commit.git(repo, "config", f"branch.{branch}.merge").stdout.strip()
    if not branch or not remote or remote == "." or not merge.startswith("refs/heads/"):
        return None
    return remote, merge.removeprefix("refs/heads/")


def ahead(repo: Path, fetch: bool = False) -> tuple[str | None, list[tuple[str, str]]]:
    """(the upstream ref, the commits HEAD has that it lacks); FETCH reads the remote first, so a
    commit pushed from elsewhere is not counted."""
    up = upstream(repo)
    if not up:
        return None, []
    if fetch:
        with contextlib.suppress(OSError):
            estate_commit.git(repo, "fetch", "-q", "--no-tags", up[0], up[1])
    ref = f"{up[0]}/{up[1]}"
    if estate_commit.git(repo, "rev-parse", "-q", "--verify", f"refs/remotes/{ref}").returncode:
        return ref, _commits(repo, "HEAD")
    return ref, _commits(repo, f"{ref}..HEAD")


def push(repo: Path, said: str, log: Path | None = None) -> tuple[str, str]:
    """Push the pin commits ahead of REPO's upstream through its own push gate: ("pushed", why),
    ("refused", why, its gate's log named) or ("committed", why not). Only goh's pin commits are
    ever pushed: an owner's commit in the span leaves it all for the owner's push, named. LOG keeps
    the gate's output (a refusal names it); without one it goes to a temporary file."""
    lead = f"{said}; " if said else ""
    ref, span = ahead(repo, fetch=True)
    if ref is None:
        return "committed", f"{lead}not pushed: its branch tracks no upstream"
    if not span:
        return "pushed", f"{lead}{ref} already has it"
    theirs = [s for sha, s in span if not is_pin_commit(repo, sha, s)]
    if theirs:
        n = counted(len(theirs), "commit")
        return "committed", (f"{lead}not pushed: {n} ahead of {ref} are its owner's "
                             f"({theirs[-1]}); their push carries the pin")  # fmt: skip
    behind = estate_commit.git(repo, "rev-list", "--count", f"HEAD..{ref}").stdout.strip()
    if behind not in ("", "0"):
        return (
            "committed",
            f"{lead}not pushed: {counted(int(behind), 'commit')} behind {ref}; pull it first",
        )
    left = int(_deadline[0] - time.monotonic())
    if left < 60:
        return (
            "committed",
            f"{lead}not pushed: the sweep's push budget is spent; the next ship's sweep pushes it",
        )
    remote, branch = ref.split("/", 1)
    with tempfile.TemporaryDirectory(dir=goh_tmp(), prefix="goh-pins-push.") as tmp:
        path = Path(log) if log is not None else Path(tmp) / "push.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        env = [f"{k}={v}" for k, v in estate_commit.env().items()]  # its gate, not this ship's
        mark = Path(tmp) / "started"  # stamped once load.py admits the push: the slot wait apart
        t0 = time.time()
        out = bounded_run.run_step(
            # Its own gate slot (GOH.SWEEPSLOTS): tools/load.py admits THIS gate and releases the
            # slot when it ends, so a sweep runs no more gates than the slots it holds and a land
            # waits at most one consumer gate. The sweep's own process holds none.
            ["env", "-i", *env, sys.executable, str(LOAD), "--max", f"{min(TIMEOUT, left) + 60}s",
             "--wait-max", f"{left}s", "0", "--", "sh", "-c", 'date +%s >"$0"; exec "$@"',
             str(mark), "git", "-C", str(repo), "push", "-q", remote, f"HEAD:refs/heads/{branch}"],
            min(TIMEOUT, left), 5, f"pins push {repo.name}", str(path),
        )  # fmt: skip
        took = int(time.time() - t0)
        started = mark.read_text().strip() if mark.exists() else ""
        waited = int(started) - int(t0) if started.isdigit() else took
        text = path.read_text(encoding="utf-8", errors="replace")
        tail = " | ".join([ln for ln in text.splitlines() if ln.strip()][-3:])
    # A push the sweep's budget killed is no verdict of its gate (GOH.SWEEPTIMEOUT): the log's tail
    # is then bash echoing the killed hook job, so it is never quoted as a reason.
    if out.timed_out:
        return "committed", (
            f"{lead}not pushed: the sweep budget ended its push after {took}s "
            f"(waited {max(0, waited)}s for a slot); the next ship sweep pushes it"
        )
    if out.code:
        kept = f"; log {path}" if log is not None else ""
        return "refused", f"{lead}NOT pushed: its push gate refused ({tail or out.code}){kept}"
    return "pushed", f"{lead}pushed {counted(len(span), 'pin commit')} to {ref}"


def width(jobs: int) -> int:
    """How many consumers the install's commits move at once: the machine's gate slots
    (GOH_GATE_SLOTS, else one per eight cores; 0 = no limit). One after another, v0.122.0-v0.124.0's
    install was the SUM of 12 consumer gates, 1974-2037 s (GOH.SHIPPROFILE). The push sweep, out of
    the ship since GOH.PINPUSHGATE, moves one at a time (lib/pin_sweep.py, GOH.LOADSOURCE)."""
    raw = os.environ.get("GOH_GATE_SLOTS", "")
    n = int(raw) if raw.isdigit() else gate_slot.default_slots()
    return max(1, min(jobs, n or jobs))


def sweep(jobs: list[tuple[str, Callable[[], tuple[str, str]]]],
          said: Callable[[str, str, str], None], row: str = "consumer",
          ok: str | tuple[str, ...] = "pushed",
          at_once: int | None = None) -> dict[str, dict[str, str]]:  # fmt: skip
    """Run each (where, job) -- one consumer's commit, or its push -- `width` at a time (AT_ONCE
    when given: the push sweep's one, lib/pin_sweep.py); each is one profile row `ROW WHERE` with
    the load at its start (failed unless its status is OK, one of them when a tuple), and SAID
    hears it as it ends."""
    fine = (ok,) if isinstance(ok, str) else ok

    def one(where: str, job: Callable[[], tuple[str, str]]) -> tuple[str, str, str]:
        t0, load1 = time.monotonic(), step_timings.load_now()
        status, why = job()
        step_timings.record(f"{row} {where}", (time.monotonic() - t0) * 1000,
                            int(status not in fine), "consumer", load1=load1)  # fmt: skip
        said(where, status, why)
        return where, status, why

    if not jobs:
        return {}
    with ThreadPoolExecutor(max_workers=at_once or width(len(jobs))) as pool:
        done = list(pool.map(lambda j: one(*j), jobs))
    return {where: {"status": status, "said": why} for where, status, why in done}


@contextlib.contextmanager
def land_held(repo: Path):
    """REPO's land lock, taken without waiting: yields None when held, else who holds it. A land
    there moves the same main a pin commit would."""
    lock = land_lock.path(repo)
    if lock is None:
        yield None
        return
    with lock.open("a+", encoding="utf-8") as f:
        try:
            lock_order.flock(f, "land", wait=False)  # the land lock, never waited on here
            holder = None
        except OSError:
            f.seek(0)
            holder = f.readline().strip() or "another land"
        try:
            yield holder
        finally:
            if holder is None:
                lock_order.unlock(f)


def record_path(result: Path | str) -> Path:
    """The sweep's record beside a goh ship result (GOH_SHIP_RESULT)."""
    return Path(result).with_suffix(".pins.json")


def write_record(result: str, tag: str, ruff: str, repos: dict[str, dict[str, str]],
                 sweep: str | None = None) -> None:  # fmt: skip
    path = record_path(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"schema": 2, "tag": tag, "ruff": ruff, "repos": repos, "sweep": sweep}
    path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")


def carry(res: dict) -> None:
    """The install's record into the ship result RES as `consumer_pins`, once the install step wrote
    it: the repos it committed in, every other one with why not, and the push sweep's record."""
    try:
        rec = json.loads(record_path(res["result"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    repos = rec.get("repos") or {}
    res["consumer_pins"] = {
        "tag": rec.get("tag"), "ruff": rec.get("ruff"),
        "committed": sorted(k for k, v in repos.items() if v.get("status") == "committed"),
        "not_committed": {k: v.get("said", "") for k, v in sorted(repos.items())
                          if v.get("status") != "committed"},
        "sweep": rec.get("sweep"),
    }  # fmt: skip


def line(res: dict) -> str | None:
    """The one line a ship says about its install's pin commits, or None (the pushes are the
    sweep's: lib/pin_sweep.py lines)."""
    c = res.get("consumer_pins")
    if not c:
        return None
    done, left = c.get("committed", c.get("pushed", [])), c.get("not_committed", {})
    head = f"consumer pins (goh {c.get('tag')}, ruff {c.get('ruff')}): {len(done)} committed"
    if not left:
        return head
    named = "; ".join(f"{k} ({v})" for k, v in left.items())
    return f"{head}; {len(left)} not: {named}"
