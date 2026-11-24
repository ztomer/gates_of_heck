"""The release canary's memo: one consumer's verdict under one side, reused while nothing that
decides it moved (GOH.CANARYMEMO).

scripts/release_canary.py swept every consumer on every land whose gates differ from the last
release: 205-232 s a land (ADVISORYLOAD 232 s, 710 cpu-s), and after one gate change every later
land until the release re-swept the same estate under the same gates -- a tests-only land too. A
side's verdict over a consumer is decided by:

  * the side's GATE CONTENT as the canary reads it: every file of its gates tree but docs/, tests/
    and *.md (release_canary's NOT_GATE_INPUTS), by path, kind and bytes -- an export's
    `.goh-head` names the commit, not its content, so a tests-only land keys as its parent;
  * the side's goh BINARY, by bytes, and the tools a step may run (lib/gate_identity.py's lines,
    its own goh-bin line aside: the canary's child never inherits GOH_BIN);
  * the private SEED store a scanner reads (GOH_SEED_DIR, else $XDG_STATE_HOME/goh/seeds), by bytes;
  * the GRACE the side runs under: `over` named, or the day -- before or after the side's own
    crate::grace date, so the day a grace ends is a new key;
  * the consumer: its common git dir and the committed HEAD sha its clone is made from.

The base side (the last release) keys alike, so it holds until the next release; the HEAD side
holds until a gate input moves. Only a verdict that judged is kept: a run that errored or was
starved (TIMED OUT) is run again next time. A record lives one day (an advisory or the date moves
without the key), in the estate state file's `release_canary` entry beside the sweep it came from,
so a reuse names that sweep. The reuse is not yet on lib/reuse.py's path table (its owner lane
held the file): GOH.CANARYMEMO2 registers it there.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_identity  # lib/, beside this file
from canary_sides import Verdict, common_dir, foreign_repo_env, fresh, git_run, kept, restored

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import counted, info

FORMAT = "release-canary memo v1"
TTL_S = 86400
CAP = 600  # records kept: ~36 consumers x 3 sides x a few gate contents
SKIP_TOP = ("docs", "tests", ".git")


def seed_store() -> Path:
    named = os.environ.get("GOH_SEED_DIR", "")
    if named:
        return Path(named)
    state = os.environ.get("XDG_STATE_HOME", "")
    return (Path(state) if state else Path.home() / ".local" / "state") / "goh" / "seeds"


def _listed(root: Path) -> list[str]:
    """Every file under root a gate may read: a checkout's tracked and untracked-unignored ones, or
    every file of a directory (an export), docs/, tests/, *.md and `.goh-head` left out."""
    rels: list[str] = []
    if (root / ".git").exists():
        r = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "-co", "--exclude-standard"],
                           capture_output=True, check=False, stdin=subprocess.DEVNULL)  # fmt: skip
        if r.returncode == 0:
            rels = [x for x in r.stdout.decode("utf-8", "replace").split("\0") if x]
    if not rels:
        rels = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() or p.is_symlink()]
    keep = (r for r in rels if r.split("/", 1)[0] not in SKIP_TOP and not r.endswith(".md"))
    return sorted(r for r in keep if r != ".goh-head" and "__pycache__" not in r.split("/"))


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for rel in _listed(root) if root.is_dir() else []:
        p = root / rel
        if p.is_symlink():
            kind, data = b"l", os.readlink(p).encode()
        elif p.is_file():
            kind, data = (b"x" if p.stat().st_mode & 0o111 else b"f"), p.read_bytes()
        else:
            continue
        h.update(rel.encode() + b"\0" + kind + b"\0" + str(len(data)).encode() + b"\0" + data)
    return h.hexdigest()


def _bytes(path: Path) -> str:
    try:
        return hashlib.sha256(Path(os.path.realpath(path)).read_bytes()).hexdigest()
    except OSError as e:
        return f"unreadable {e.strerror}"


def grace_token(grace: str | None, over_from: int | None, now: float) -> str:
    if grace:
        return f"grace {grace}"
    if over_from is None:
        return "grace day, none"
    return f"grace day, {'over' if now >= over_from else 'within'} {over_from}"


def side_key(gohdir: Path, binary: Path, grace: str, env: dict[str, str]) -> str:
    """One side's half of the key, computed once a run: everything but the consumer."""
    tools = [t for t in gate_identity.tools() if not t.startswith("tool goh-bin ")]
    kept = [f"env {k}={env[k]}" for k in sorted(env)]
    lines = [FORMAT, f"gates {tree_digest(gohdir)}", f"bin {_bytes(binary)}", *tools,
             f"seeds {tree_digest(seed_store())}", grace, *kept]  # fmt: skip
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def key_sides(sides, over_from: Callable[[str], int | None]) -> None:
    """Each side's half of the key, set on it (`side.memo`); None sides skipped."""
    live = {k: os.environ[k] for k in ("GOH_LIVE",) if k in os.environ}
    for s in sides:
        if s is not None:
            grace = grace_token(s.grace, over_from(str(s.gohdir)), time.time())
            s.memo = side_key(s.gohdir, s.bin, grace, live)


def consumer_head(repo: Path) -> str:
    r = git_run(repo, "rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else ""


def key(side: str, repo_id: str, sha: str) -> str:
    return hashlib.sha256(f"{FORMAT}\n{side}\nrepo {repo_id}\nsha {sha}".encode()).hexdigest()


class Memo:
    """The run's view of the recorded verdicts: `get` a live one, `put` a fresh one; `records`
    is what the state file keeps (every live record, newest first, CAP of them)."""

    def __init__(self, old: dict | None, sweep: dict[str, str], reuse: bool = True) -> None:
        self.now = int(time.time())
        self.sweep, self.reuse, self.lock = sweep, reuse, threading.Lock()
        self.records = {k: v for k, v in (old or {}).items() if self._live(v)}
        self.reused: dict[str, list[str]] = {}
        self.swept: dict[str, list[str]] = {}
        self.sources: set[str] = set()

    def _live(self, v: object) -> bool:
        epoch = v.get("epoch") if isinstance(v, dict) else None
        return isinstance(epoch, int) and 0 <= self.now - epoch < TTL_S

    def get(self, k: str, where: str, label: str) -> dict | None:
        with self.lock:
            got = self.records.get(k) if self.reuse else None
            if got is not None:
                self.reused.setdefault(where, []).append(label)
                sw = got.get("sweep") if isinstance(got.get("sweep"), dict) else {}
                self.sources.add(
                    f"{sw.get('ran_at', '?')} (HEAD {str(sw.get('head_sha', '?'))[:12]})"
                )
            return got

    def put(self, k: str, where: str, label: str, verdict: dict) -> None:
        with self.lock:
            self.swept.setdefault(where, []).append(label)
            self.records[k] = {**verdict, "epoch": self.now, "sweep": self.sweep}

    def kept(self) -> dict:
        newest = sorted(self.records.items(), key=lambda kv: -int(kv[1].get("epoch", 0)))
        return dict(newest[:CAP])

    def summary(self) -> dict:
        """{reused: {where: [side]}, swept: {where: [side]}, from: [the sweeps it reused]}."""
        return {"reused": self.reused, "swept": self.swept, "from": sorted(self.sources)}


class CloneError(Exception):
    pass


class Runs:
    """One consumer's side runs: each answered by the memo while its key matches, else `run` in a
    clone of the consumer's committed HEAD, made at the first miss."""

    def __init__(self, repo: Path, where: str, scratch: Path, memo: Memo,
                 run: Callable[..., Verdict]) -> None:  # fmt: skip
        self.repo, self.where, self.tree, self.memo, self.run = (
            repo,
            where,
            scratch / "tree",
            memo,
            run,
        )
        self.rid, self.sha, self.cloned, self.used = common_dir(repo), "", False, False

    def side(self, side) -> tuple[Verdict, float]:
        """(the side's verdict over this consumer, the seconds its run spent: 0 for a reuse)."""
        self.sha = self.sha or consumer_head(self.repo)
        k = key(side.memo, self.rid, self.sha) if side.memo and self.sha else ""
        if k and (got := self.memo.get(k, self.where, side.label)) is not None:
            return restored(got), 0.0
        if not self.cloned:
            self._clone()
        elif self.used:
            fresh(self.tree)
        self.used, t0 = True, time.monotonic()
        v = self.run(side, self.tree)
        secs = round(time.monotonic() - t0, 1)
        if side.memo and self.sha and (keep := kept(v)) is not None:
            self.memo.put(key(side.memo, self.rid, self.sha), self.where, side.label,
                          {**keep, "seconds": secs})  # fmt: skip
        return v, secs

    def _clone(self) -> None:
        env = {**foreign_repo_env(), "GIT_LFS_SKIP_SMUDGE": "1"}
        argv = ["git", "clone", "-q", "--shared", "--no-tags", str(self.repo), str(self.tree)]
        c = subprocess.run(argv, env=env, capture_output=True, text=True, check=False,
                           stdin=subprocess.DEVNULL)  # fmt: skip
        if c.returncode != 0:
            raise CloneError(c.stderr.strip())
        self.cloned, self.sha = True, git_run(self.tree, "rev-parse", "HEAD").stdout.strip()


def records(state: Path, entry: str) -> dict:
    """The memo's records, as the last canary left them in the state file's `entry`."""
    import estate_registry  # lib/'s: the estate state in goh.db (GOH.STATEDBS3)

    try:
        got = estate_registry.read_state(state).get(entry, {}).get("memo_records")
    except AttributeError:
        return {}
    return got if isinstance(got, dict) else {}


def say(memo: dict) -> None:
    """Which sweep a land reused, and what it swept afresh."""
    reused, swept = memo.get("reused") or {}, memo.get("swept") or {}
    if not reused:
        return
    n = counted(sum(len(v) for v in reused.values()), "side verdict")
    info(f"release canary: reused {n} over {counted(len(reused), 'consumer')} from the sweep of")
    fresh_ones = ", ".join(f"{w} ({', '.join(v)})" for w, v in sorted(swept.items()))
    info(f"  {', '.join(memo.get('from') or ['?'])}; swept afresh: {fresh_ones or 'none'}")
