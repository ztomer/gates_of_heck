"""The pre-step's worker shares (GOH.REMOTECARRY): the Linux worker starts on half of what it
should take, and is handed more as it frees, so both sides end together.

THE MEASURE (2026-11-25, the 32 newest pre-steps with a share; median 480 s, 408 of them the Mac's
judges and rest). One split, made before the first test ran, left the worker done 115 s before the
Mac at the median (26 of 32; |gap| 120 s): the Mac's pace is 3.3 to 13 table seconds a second with
the load and its workers, and no learned rate says which. The judges were the Mac's alone and half
of its wall, so even a perfect split of the rest gains only 34 s; with the Linux-safe judges and
units of the Mac's running queue in play the ideal gain is 69 s. Replayed, a better-rated static
split takes 12 of it (|gap| 36-120 s), a re-split at the judges' end 27, and half first and the rest
handed over when the worker frees 41-61, the sides ending 8-14 s apart.

THE SHARES, all launched by one thread (`Feed._work`), each `goh remote-shard` as before:
  0. `--prepare`: the worker's tree and bin/goh, while the Mac's first run plans its carry;
  1. half (PART) of the balanced split (lib/remote_lane.py `split`), taken from `pool`: the rest's
     Linux-safe nodes (a Rust change's: those that run no binary, none on its retry), while the Mac
     runs the judges. A node the carry answers whole (`plan.json`)
     is costed at nothing and not sent: it stays on the Mac, whose own carry deselects it;
  2. each time a share ends green, the Mac's running pytest is asked (tests/_worker_feed.py) how
     much the worker may still take; it comes from the pool while that is open, then from the units
     the controller takes out of its queue. WAVES at most; none after a share that is not green.

NEVER IN NEITHER. `rest` gives the Mac every node not in `given`, under the lock that gives. A unit
leaves the Mac's queue only after the reply naming it is on disk, and `end` reads every reply: what
was given and launched is a Share, judged by `remote_lane.end` (a worker death, a stall, a skip
mismatch, an unfinished share end as there, the share whole on the Mac); what was given and never
launched runs on the Mac. A red share found at the judges' end (`early`) is re-run then, so a judge
the worker took is red before the slow tests.

Pinned by tests/test_remote_feed.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import fast_tests, remote_lane, remote_saved, tmp_root
from lib.remote_lane import Share
from lib.remote_rates import load_rates
from tui.lib import counted, info

ENV = "GATES_OF_HECK_LAND_FEED"  # tests/_worker_feed.py reads it
PART, WAVES = 0.5, 4
LEAST_S = 20.0  # a share that would save less than this is not worth its bring-up
SHARD_S = 8.0  # a later shard's probe, sync, warm build and ssh (1.6, 0.6, 0.7, 5 s)
BRING_S = 15.0  # a shard's session on the worker before its tests run at full width
PLAN_S = 60.0  # the Mac's first run says its carry within this, or the split is blind to it
POLL_S = 0.2


def _put(path: Path, doc: object) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    tmp.replace(path)


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def prepare(root: Path) -> subprocess.Popen[bytes]:
    argv = [sys.executable, str(root / "tools" / "remote_shard.py"), "--tree", str(root),
            "--no-local", "--prepare"]  # fmt: skip
    return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL)  # fmt: skip


class Feed:
    def __init__(self, root: Path, judges: list[str], rest: list[str], far: list[str],
                 record: str = "", rust: bool = False) -> None:  # fmt: skip
        """REST runs on the Mac whatever the worker does; FAR, the rest the worker may take. RUST
        (GOH.RUSTPRECARRY): REST is the measured run, which no share takes units of, whose wall is
        no pace of the Mac's, and FAR runs beside it (lib/land_rustcov.py `split`)."""
        self.root, self.judges, self.record, self.rust = root, judges, record, rust
        self.all_rest = [*rest, *far]
        self.safe = remote_lane.listed(root, remote_lane.LINUX_SAFE) - remote_lane.unsafe()
        self.table = fast_tests.load()
        self.skip: set[str] = set()  # what the Mac's carry answers
        before = judges or rest  # a Mac run before the far nodes', or beside it
        self.pool = [n for n in far if remote_lane.module(n) in self.safe] if before else []
        self.first = threading.Event()  # the first share is decided
        self.given: list[str] = []
        self.shares: list[Share] = []
        self.ended: set[int] = set()
        self.notes: list[dict] = []
        self.lock, self.over, self.stopped = threading.Lock(), threading.Event(), False
        self.open = True  # the rest is not handed to the Mac yet
        self.asked = 0
        self.began = time.time()
        self.prep: subprocess.Popen[bytes] | None = None
        self.thread: threading.Thread | None = None
        self.fault: BaseException | None = None
        self.dir = ""

    # -- the Mac's side: tools/affected_tests.py `run` ----------------------------------------

    def start(self) -> Feed:
        self.dir = tempfile.mkdtemp(prefix="goh-feed-", dir=tmp_root.goh_tmp())
        self.prep = prepare(self.root)
        self.thread = threading.Thread(target=self._guarded, name="goh-feed", daemon=True)
        self.thread.start()
        return self

    def words(self) -> list[str]:
        """The words before a Mac run's pytest: the directory its controller answers in."""
        return ["env", f"{ENV}={self.dir}"] if self.dir else []

    def rest(self) -> list[str]:
        """The Mac's part of the rest, the pool closed: what was not given to the worker. A first
        share not yet decided is waited for (a Rust change's measured run may be the first)."""
        if self.thread is not None:
            self.first.wait(PLAN_S)
        with self.lock:
            self.open = False
            gone = set(self.given)
            if self.asked and not (Path(self.dir) / f"reply-{self.asked}.json").exists():
                self._ask()  # the next run answers: its queue is all there is now
            return [n for n in self.all_rest if n not in gone]

    def early(self, merge: Callable[[Path, Path], None]) -> int:
        """Between the Mac's two runs: a share that has ended red is re-run now."""
        for i, share in enumerate(list(self.shares)):
            if i not in self.ended and share.proc.poll() == 1 and (rc := self._end(i, merge, 0.0)):
                return rc
        return 0

    def cancel(self) -> None:
        """The Mac went red: every share is stopped, and its verdict stands."""
        self.stopped = True
        self._close()
        for i, share in enumerate(self.shares):
            if i not in self.ended:
                remote_lane.cancel(share)
        self._clean()

    def end(self, merge: Callable[[Path, Path], None],
            run_local: Callable[[list[str]], int] | None = None) -> int:  # fmt: skip
        """The worker's verdicts joined (module doc, NEVER IN NEITHER); the first red stops all."""
        mac_end, mac_s, rc, i = time.time(), self._executed(), 0, 0
        self.over.set()
        while not rc:
            if i < len(self.shares):
                rc = 0 if i in self.ended else self._end(i, merge, mac_s, mac_end, run_local)
                i, mac_s = i + 1, 0.0  # one reading of the Mac's pace
            elif self.thread is not None and self.thread.is_alive():
                self.thread.join(POLL_S)
            else:
                break
        if rc:
            self.cancel()
            return rc
        self._close()
        lost = self._orphans()
        if self.fault is not None:
            info(f"pre-land: the worker's feed stopped: {self.fault!r}")
        if lost:
            info(f"pre-land: {counted(len(lost), 'test')} given to the worker, never sent: the Mac "
                 "runs them now")  # fmt: skip
            rc = remote_lane.on_mac(self.root, lost, self.record, merge, run_local)
        if len(self.notes) > 1 and (took := os.environ.get("GOH_LAND_PRE_TOOK")):
            remote_lane.leave(took, remote_saved.whole(self.notes))
        self._clean()
        return rc

    def _clean(self) -> None:
        if self.dir:
            shutil.rmtree(self.dir, ignore_errors=True)

    def _end(self, i: int, merge: Callable[[Path, Path], None], mac_s: float,
             mac_end: float | None = None,
             run_local: Callable[[list[str]], int] | None = None) -> int:  # fmt: skip
        share = self.shares[i]
        wall = (mac_end or time.time()) - self.began  # the Mac's, which began before the share
        share.mac_s = mac_s * ((mac_end or 0.0) - share.started) / wall if wall > 0 else 0.0
        self.ended.add(i)
        rc = remote_lane.end(self.root, share, self.record, merge, run_local, mac_end=mac_end)
        self.notes.append(share.note)
        return rc

    def _close(self) -> None:
        """The thread ended; a stopped one's processes killed until it has (one may start yet)."""
        self.over.set()
        while True:
            for proc in (self.prep, *(s.proc for s in self.shares)) if self.stopped else ():
                if proc is not None and proc.poll() is None:
                    proc.kill()
            if self.thread is None or not self.thread.is_alive():
                return
            self.thread.join(POLL_S)

    def _executed(self) -> float:
        """The table seconds of what the Mac RAN: its record's passes, never what it carried; none
        of a run that builds and instruments (GOH.RUSTPRECARRY)."""
        on = self.record and self.record != os.devnull and not self.rust
        got = _read(Path(self.record)) if on else None
        return sum(self.table.get(n, 0.0) for n in (got or {}).get("passed", ()))

    def _orphans(self) -> list[str]:
        sent = {n for s in self.shares for n in s.nodes}
        replies = sorted(Path(self.dir).glob("reply-*.json")) if self.dir else []
        named = [n for r in replies for n in (_read(r) or {}).get("nodes", ())]
        return sorted({*self.given, *named} - sent)

    # -- the worker's side: one thread ----------------------------------------------------------

    def _guarded(self) -> None:
        try:
            self._work()
        except Exception as e:  # noqa: BLE001 -- `end` says it and runs what was given and not sent
            self.fault = e

    def _work(self) -> None:
        try:  # only a judges' run says its plan before the rest begins
            plan = self._wait(Path(self.dir) / "plan.json", PLAN_S) if self.judges else None
            self.skip = set((plan or {}).get("skip", ()))
            nodes = self._first()
        finally:
            self.first.set()
        if self.prep is not None:
            self.prep.wait()
        while not self.stopped and (nodes or (len(self.shares) < WAVES and not self.over.is_set())):
            if not (nodes := nodes or self._next()):
                return
            share = remote_lane.launch(self.root, nodes, 0.0, later=bool(self.shares))
            self.shares.append(share)
            info(f"pre-land: {counted(len(nodes), 'Linux-safe test')} on the remote worker (share "
                 f"{len(self.shares)}), beside the Mac's")  # fmt: skip
            nodes = []
            if share.proc.wait() != 0 or os.environ.get("GOH_REMOTE_SHARE"):  # a fixed share is one
                return

    def _wait(self, path: Path, seconds: float) -> dict | None:
        end = time.monotonic() + seconds
        while (doc := _read(path)) is None and time.monotonic() < end:
            if self.over.wait(POLL_S):
                return _read(path)
        return doc

    def secs(self, nodes: list[str]) -> dict[str, float]:
        """Each node's table seconds of what will RUN: nothing for one the carry answers whole."""
        runs = {n: s for n, s in self.table.items() if n not in self.skip}
        left, known = remote_lane.cost(nodes, runs), remote_lane.held(runs)
        carried = remote_lane.held(dict.fromkeys(self.skip, 0.0))
        return {n: 0.0 if n in self.skip or (n in carried and n not in known) else left[n]
                for n in nodes}  # fmt: skip

    def _first(self) -> list[str]:
        """Half of the balanced split, of the pool; [] when there is no pool (the Mac is asked)."""
        secs, rates = self.secs([*self.judges, *self.all_rest]), load_rates()
        runs = [n for n in self.pool if secs[n] > 0]
        could = [*runs, *(n for n in self.judges if remote_lane.module(n) in self.safe
                          and not self.rust)]  # fmt: skip
        others = sum(s for n, s in secs.items() if n not in set(could))
        fixed = bool(os.environ.get("GOH_REMOTE_SHARE"))
        sent, _ = remote_lane.split(self.root, could, secs, rates, mac_else=others)
        took = {n: rates.worker_s(n, secs[n]) for n in sent}
        room = sum(took.values()) * (1.0 if fixed else PART)
        out: list[str] = []
        for n in sorted((n for n in runs if n in took), key=lambda n: -secs[n]):
            if room > 0:
                out.append(n)
                room -= took[n]
        return self._give(out)

    def _give(self, nodes: list[str]) -> list[str]:
        """NODES out of the pool, while it is open."""
        with self.lock:
            out = [n for n in nodes if n in set(self.pool)] if self.open else []
            self.pool = [n for n in self.pool if n not in set(out)]
            self.given += out
            return out

    def _ask(self) -> None:
        """The ask the Mac's run answers (the lock held, or its caller's alone)."""
        rates = load_rates()
        k, ratio = rates.ratio / max(rates.worker_width, 1.0), rates.ratio
        secs = self.secs([n for n in self.all_rest if n not in set(self.given)])
        pool = self.pool if self.open else []
        if self.shares and (wall := self._suite_s(self.shares[-1])):
            ran = sum(self.secs(self.shares[-1].nodes).values())
            k = max(0.02, wall - BRING_S) / ran if ran > 0 else k
        _put(Path(self.dir) / "ask.json", {
            "n": self.asked, "safe": [] if self.rust else sorted(self.safe),
            "pool_s": sum(secs[n] for n in pool),
            "after_s": sum(secs.values()) if self.open else 0.0, "k": k, "ratio": ratio,
            "overhead_s": SHARD_S + BRING_S,
            "mac_width": rates.mac_width, "least_s": LEAST_S,
            "part": 1.0 if self.shares else PART})  # fmt: skip

    def _next(self) -> list[str]:
        """What the Mac's run hands over; [] when it has nothing worth a share, or is done."""
        while not self.stopped and not self.over.is_set():
            with self.lock:
                self.asked += 1
                self._ask()
            reply = Path(self.dir) / f"reply-{self.asked}.json"
            while (doc := _read(reply)) is None:
                if self.over.wait(POLL_S):
                    doc = _read(reply)  # answered as the Mac ended
                    break
            want = float((doc or {}).get("want_s", 0.0))
            if doc is None or self.stopped or want <= 0:
                return []
            with self.lock:
                self.given += doc["nodes"]
            out = [*self._give(self._costliest(want)), *doc["nodes"]]
            if out or self.open:  # else the pool closed as it was answered: asked again
                return out
        return []

    def _costliest(self, want: float) -> list[str]:
        secs, out = self.secs(self.pool), []
        for n in sorted(self.pool, key=lambda n: -secs[n]):
            if want > 0 and secs[n] > 0:
                out.append(n)
                want -= secs[n]
        return out

    @staticmethod
    def _suite_s(share: Share) -> float:
        try:
            doc = ET.fromstring(share.junit.read_text())
        except (OSError, ET.ParseError):
            return 0.0
        suite = doc if doc.tag == "testsuite" else doc.find(".//testsuite")
        return float(suite.get("time", "0")) if suite is not None else 0.0


def begin(root: Path, judges: list[str], rest: list[str], far: list[str], record: str = "",
          rust: bool = False) -> Feed:  # fmt: skip
    """The pre-step's feed (`Feed`); an idle one (nothing sent) when the share is off, or when no
    test the worker may take is Linux-safe: on a Rust change only FAR's, none on its retry."""
    feed = Feed(root, judges, rest, far, record, rust)
    may = far if rust else [*judges, *far]
    some = any(remote_lane.module(n) in feed.safe for n in may)
    return feed if remote_lane.off() or not some else feed.start()
