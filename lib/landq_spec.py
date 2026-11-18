"""A red batch's halves gate at once (GOH.LANDBISECTPAR): the batch's speculative second run.

WHY. lib/landq_batch.py bisected a red stack serially: the first half restacked on main and gated,
then the second half on whatever landed, one full land gate after the other. On 2026-11-17
SELECTAUDIT spent about 45 min in a 2-item batch whose first gate was red. The machine has two gate
slots (GOH_GATE_SLOTS), and after a red the second is the one worth spending.

WHAT RUNS. When a stack is red and splits into halves A and B, B is stacked on main too and its
tip's land.sh starts in a second thread while A settles as before -- the same land.sh, admitted for
its gate slot by load.py like any land (no share is set here: the admitted count decides,
GOH.SHAREGROW). Only one such run is in flight, so a batch never holds more than two gates; a
split inside A while it runs is serial. A green batch never gets here: it is one gate, alone.

WHY B ON MAIN, NOT A+B. A+B is the stack that was just red: its tree is known, so gating it again
is a wasted slot. The run worth having beside A is the one the serial queue would gate next when A
lands nothing: B on main.

WHAT IS USED. After A settles, main is re-read under the land lock the lander holds:
  * main is the base B was stacked on (nothing of A landed): the run IS B's gate. The batch writes
    `go` on the hold pipe; land.sh, holding a green for that word (tools/land.sh, GOH_LAND_HOLD_FD),
    fast-forwards main as any land does -- B lands with no further gate. Red: its tree is recorded
    red and B bisects from it without gating it again. Past GOH_LAND_TIMEOUT: B is red, `timed
    out`, and not bisected, as any batch run.
  * main moved (something of A landed): B on the old main is not B's tree any more. The run is
    discarded -- its pipe closed (a held green exits 3, landing nothing) and its process group
    stopped if it is still gating -- and B settles on the new main as before. A speculative land.sh
    never rebases (exit 3), so the tree it judged is always the one stacked here.
A run stacking B on main that conflicts is not started (that conflict is no verdict on B over A).
Nor is one on a machine with ONE gate slot (GOH_GATE_SLOTS=1, or cores // 8 < 2: the 3-core CI
runner, any Mac under 16 cores; GOH.SPECONESLOT): B's run took the only slot first and A waited
behind it, so the bisect is serial there and lander.log names why.
Every run started counts in the batch's `gates`, discarded ones too: `goh land --report` reads
gates per landed item and the wall from them.
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import admission
import killtree
import land_slot

if TYPE_CHECKING:
    from landq_batch import Batch, Item

MARK = "GOH_LAND_HOLD_FD"  # a land.sh that names it holds a speculative green for the word
DROPPED = 3  # land.sh's exit when it lands nothing on the batch's word


def able(script: Path) -> bool:
    """Whether SCRIPT (the land.sh the tip's lane runs) knows the hold: one that does not would
    fast-forward a speculative green at once, so the batch stays serial for it."""
    try:
        return MARK in script.read_text(errors="replace")
    except OSError:
        return False


@dataclass
class Spec:
    """One speculative land.sh: started at once, its verdict read once."""

    stacked: list[Item]
    base: str
    tree: str
    log: Path
    word: int
    timeout: int
    grace: float
    proc: subprocess.Popen | None = None
    state: str = ""
    code: int = 0
    begun: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)

    def run(self, argv: list[str], hold: int) -> None:
        """The thread: land.sh started, given the pipe's read end HOLD, and reaped."""
        try:
            with self.log.open("ab") as out, subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                pass_fds=(hold,), start_new_session=True,
            ) as proc:  # fmt: skip
                self.proc = proc
                os.close(hold)
                self.begun.set()
                try:
                    self.code = proc.wait(timeout=self.timeout)
                    self.state = "landed" if self.code == 0 else "red"
                except subprocess.TimeoutExpired:
                    killtree.stop_group(proc.pid, self.grace, proc)
                    proc.wait()
                    self.state = "timeout"
        except OSError as e:
            if self.proc is None:
                os.close(hold)
            self.state, self.code = "red", 127
            with self.log.open("a") as f:
                f.write(f"landq: cannot start the speculative land: {e}\n")
        finally:
            self.begun.set()
            self.done.set()

    def say(self, word: str) -> None:
        """Write WORD (or nothing) and close: land.sh reads one line or EOF."""
        if self.word < 0:
            return
        try:
            if word:
                os.write(self.word, f"{word}\n".encode())
        except OSError:
            pass  # land.sh already gone (red, or exited): its exit code says what happened
        os.close(self.word)
        self.word = -1

    def drop(self) -> None:
        """Discard the run: a held green exits 3 on EOF, a gating one has its group stopped."""
        self.say("")
        self.begun.wait()
        if not self.done.is_set() and self.proc is not None:
            killtree.stop_group(self.proc.pid, self.grace, self.proc)
        self.done.wait()


def start(b: Batch, second: list[Item]) -> Spec | None:
    """SECOND stacked on main and its tip's land.sh started, holding; None when it is not worth a
    slot (another run in flight, a land.sh without the hold, one gate slot, a conflict, or a tree
    known red)."""
    if b.spec is not None or not able(b.kit.land_script(Path(second[-1].req["worktree"]))):
        return None
    if (slots := admission.configured()[1]) == 1:  # 0 is slots off: every gate runs at once
        b.kit.say(f"batch {b.id}: {slots} gate slot on this machine (GOH_GATE_SLOTS) -- the halves "
                  "gate serially, no speculative run beside the first")  # fmt: skip
        return None
    base = b.git(b.main, "rev-parse", "HEAD")
    stacked = b.stack(second, trial=True)
    if stacked is None:
        return None
    top = stacked[-1]
    tree = b.git(Path(top.req["worktree"]), "rev-parse", "HEAD^{tree}")
    if tree in b.red_trees:
        return None
    b.gates += 1
    log = b.q / "logs" / f"{b.id}.gate{b.gates}.log"
    wt = Path(top.req["worktree"])
    b.kit.say(f"batch {b.id}: gate {b.gates} on {top.tip[:8]}, {len(stacked)} stacked on main "
              f"{base[:8]} beside the first half (speculative)")  # fmt: skip
    read, write = os.pipe()
    argv = ["env", *b.kit.unset, *land_slot.label(top.req), f"{MARK}={read}", "bash", "-c",
            'cd -- "$1" && exec bash "$2"', "land", str(wt),
            str(b.kit.land_script(wt))]  # fmt: skip
    spec = Spec(stacked, base, tree, log, write, b.kit.timeout, b.kit.grace)
    threading.Thread(target=spec.run, args=(argv, read), name=f"landq-spec-{b.id}",
                     daemon=True).start()  # fmt: skip
    return spec


def split(b: Batch, first: list[Item], second: list[Item]) -> None:
    """Settle FIRST, then SECOND -- SECOND's run on main beside FIRST's when it may be used."""
    spec = start(b, second)
    if spec is None:
        b.settle(first)
        b.settle(second)
        return
    b.spec = spec
    try:
        b.settle(first)
        if b.git(b.main, "rev-parse", "HEAD") != spec.base:
            b.kit.say(f"batch {b.id}: the first half landed -- {spec.log.name} judged main "
                      f"{spec.base[:8]}, discarded")  # fmt: skip
            spec.drop()
        else:
            spec.say("go")
            spec.done.wait()
            if use(b, spec):
                return
    finally:
        spec.drop()
        b.spec = None
    b.settle(second)


def use(b: Batch, spec: Spec) -> bool:
    """SPEC's verdict on SECOND, main being its base: True when it settled every item."""
    why = f"timed out after {spec.timeout}s" if spec.state == "timeout" else f"exit {spec.code}"
    if spec.state == "landed":
        b.landed(spec.stacked, spec.log)
        return True
    if spec.state == "timeout":
        for it in spec.stacked:
            b.outcome(it, "red", b.own_log(it, spec.log, why), why=why)
        return True
    if spec.code != DROPPED and spec.code >= 0:  # a signal is no verdict: B is gated again
        b.red_trees[spec.tree] = (spec.log, why)
    return False
