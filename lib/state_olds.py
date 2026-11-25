"""lib/state_olds.py -- `goh state olds --since X`: what still runs goh without commit X, one line
each, exit 1 while there is any (GOH.OLDWRITERS; the data plan's C(X)).

A schema step that older code cannot write through waits for this to be empty:

    export     a process whose command names a HEAD export of a commit without X
    checkout   a process whose command names a worktree that stands at a commit without X
    started    a process started before its worktree's CURRENT span with X began: HEAD's reflog,
               walked back from its newest entry to the first commit without X. Not "first stood
               at X": a checkout that went back and returned ran old code in between (L12)
    holder     a process that holds goh.db open (lsof) and whose copy of goh nothing above names
    worktree   a worktree of the checkout whose HEAD lacks X
    binary     an installed goh whose build stamp is no commit's that has X, or a process running
               a goh binary since replaced on disk

A reflog that does not reach HEAD, or a commit git cannot find, proves nothing: named, never passed.
Pinned by tests/test_state_olds.py.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goh_db
import goh_root

EXPORT = re.compile(r"/goh/head/([0-9a-f]{40})\b")
MOST_STAMPS = 60  # a stamp costs about 0.25 s


@dataclass(frozen=True)
class Proc:
    pid: int
    start: float
    cmd: str


def _run(*argv: str, timeout: float = 60) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout,
                              env=env, stdin=subprocess.DEVNULL).stdout  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return ""


def processes() -> list[Proc]:
    out = []
    for line in _run("ps", "-axo", "pid=,lstart=,command=").splitlines():
        f = line.split(None, 6)
        if len(f) == 7:
            out.append(Proc(int(f[0]), time.mktime(time.strptime(" ".join(f[1:6]))), f[6]))
    return out


def holders() -> set[int]:
    db = str(goh_db.path())
    got = _run("lsof", "-t", db, f"{db}-wal")
    return {int(p) for p in got.split() if p.isdigit()}


@dataclass
class Checkout:
    """The gates checkout at ROOT, asked whether a commit has X."""

    root: Path
    x: str
    _has: dict[str, bool] = field(default_factory=dict)

    def git(self, *args: str) -> str:
        return _run("git", "--no-optional-locks", "-C", str(self.root), *args)

    def has(self, sha: str) -> bool:
        if sha not in self._has:
            got = subprocess.run(
                ["git", "--no-optional-locks", "-C", str(self.root), "merge-base",
                 "--is-ancestor", self.x, sha], capture_output=True, check=False, timeout=60,
                env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
                stdin=subprocess.DEVNULL)  # fmt: skip
            self._has[sha] = got.returncode == 0
        return self._has[sha]

    def worktrees(self) -> dict[str, str]:
        """{worktree: its HEAD}."""
        out, cur = {}, ""
        for line in self.git("worktree", "list", "--porcelain").splitlines():
            key, _, value = line.partition(" ")
            if key == "worktree":
                cur = value
            elif key == "HEAD":
                out[cur] = value
        return out

    def since(self, wt: str, head: str) -> float | None:
        """When WT's current unbroken span with X began (epoch), None when its reflog cannot say."""
        rel = _run("git", "-C", wt, "rev-parse", "--absolute-git-dir").strip()
        try:
            rows = Path(rel, "logs", "HEAD").read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        began = None
        for i, line in enumerate(reversed(rows)):
            m = re.match(r"[0-9a-f]+ ([0-9a-f]+) .*> (\d+) [+-]\d{4}(\t|$)", line)
            if not m or (i == 0 and m.group(1) != head):
                return began
            if not self.has(m.group(1)):
                break
            began = float(m.group(2))
        return began

    def stamps(self) -> Iterable[str]:
        """The build stamp of every commit with X on this HEAD, newest first (one for each change
        of a build input: between two, the stamp is the same)."""
        script = str(self.root / "lib" / "goh_version.sh")
        inputs = _run("bash", script, "--inputs", str(self.root), "HEAD").split()
        moved = self.git("rev-list", f"{self.x}..HEAD", "--", *inputs).split()
        for rev in ["HEAD", *moved[:MOST_STAMPS], self.x]:
            yield bare(_run("bash", script, "--stamp", str(self.root), rev))


def bare(stamp: str) -> str:
    """A build stamp without its release field."""
    return " ".join(w for w in stamp.split() if re.fullmatch(r"[0-9a-f]{40}", w))


def _code(p: Proc, co: Checkout, trees: dict[str, str], spans: dict[str, float | None]) -> str:
    """Why P runs old code by the copy its command names; "" when not, or it names none."""
    if m := EXPORT.search(p.cmd):
        return "" if co.has(m.group(1)) else f"export of {m.group(1)[:12]}"
    for wt in sorted(trees, key=len, reverse=True):
        if f"{wt}/" not in f"{p.cmd}/":
            continue
        if not co.has(trees[wt]):
            return f"checkout {wt} lacks it"
        if wt not in spans:
            spans[wt] = co.since(wt, trees[wt])
        began = spans[wt]
        if began is None:
            return f"checkout {wt}: its reflog does not say since when it has it"
        return f"started before {wt} (again) had it" if p.start <= began else ""
    return ""


def _named(p: Proc, trees: dict[str, str]) -> bool:
    return bool(EXPORT.search(p.cmd)) or any(f"{wt}/" in f"{p.cmd}/" for wt in trees)


def binaries(root: Path, procs: list[Proc]) -> list[Path]:
    """The installed goh binaries: the checkout's, PATH's, and each one a process runs."""
    got = [root / "bin" / "goh", Path(shutil.which("goh") or ""),
           *(Path(p.cmd.split()[0]) for p in procs)]  # fmt: skip
    return sorted({p for p in got if p.name == "goh" and p.is_absolute() and p.is_file()})


def _binaries(
    co: Checkout, procs: list[Proc], asked: Callable[[Path], str], found: list[Path]
) -> list[str]:
    out, good = [], None
    for b in found:
        said = bare(asked(b))
        if good is None:
            good = list(co.stamps())
        if not said or said not in good:
            out.append(
                f"binary {b}: its build stamp ({said[:12] or 'none'}) is no commit's with it"
            )
        for p in procs:
            if p.cmd.split()[0] == str(b) and p.start <= b.stat().st_mtime:
                out.append(f"binary {p.pid}: runs a {b} that was replaced after it started")
    return out


def _asked(b: Path) -> str:
    return _run(str(b), "source-tree")


def olds(co: Checkout, procs: list[Proc], open_by: set[int], found: list[Path] | None = None,
         asked: Callable[[Path], str] = _asked) -> list[str]:  # fmt: skip
    """One line for each thing that runs, or will run, goh without X."""
    trees, spans = co.worktrees(), {}
    out = []
    for p in procs:
        if p.pid == os.getpid():
            continue
        if why := _code(p, co, trees, spans):
            out.append(f"process {p.pid}: {why}")
        elif p.pid in open_by and not _named(p, trees):
            out.append(f"holder {p.pid}: has goh.db open; its copy of goh is not named")
    out += [f"worktree {wt}: HEAD {h[:12]} lacks it" for wt, h in trees.items() if not co.has(h)]
    gone = open_by - {p.pid for p in procs}
    out += [f"holder {pid}: has goh.db open; not in the process list" for pid in sorted(gone)]
    found = binaries(co.root, procs) if found is None else found
    return out + _binaries(co, procs, asked, found)


def main(since: str) -> int:
    from tui.lib import err, info, ok

    root = Path(goh_root.live_root_for(str(goh_db.db_core.ROOT)) or goh_db.db_core.ROOT)
    x = Checkout(root, "").git("rev-parse", "--verify", "--quiet", f"{since}^{{commit}}").strip()
    if not x:
        err(f"olds: {since} is no commit of {root}")
        return 2
    open_by = holders()  # first: a holder that ended since is then no process, never unnamed
    procs = processes()
    lines = olds(Checkout(root, x), procs, open_by & {p.pid for p in procs})
    for line in lines:
        info(line)
    (err if lines else ok)(f"olds: {len(lines)} without {x[:12]} ({len(procs)} processes read)")
    return 1 if lines else 0
