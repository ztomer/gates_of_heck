"""lib/land_judge_steps.py -- main's version of each gate step a lane changed, over the lane's tree
(GOH.ONEGATE).

    choose(main, tip)     [Choice]: main's steps, each `changed`, `same`, `dropped` or `deleted`
    judge(choices, ...)   [Step]: each one's outcome; a step whose closure did not move is not run

WHICH STEPS. Main's GOH_CI_STEPS, read from its export. A step the lane changed is one whose goh
closure (lib/step_closure.py, the runner's in every one; the whole content where the reader cannot
place the step) differs between the two exports. A step main has and the tip's list has not is
`dropped`; one whose script the tip's tree has not is `deleted`; main's version of both runs too.
A land whose gate is another command (GOH_LAND_GATE) dropped the list itself.

HOW ONE RUNS. The step's words that name a file of main's gate code become main's export's path
(`./gates/structural.sh` in the lane's tree is the lane's own code: why a second gate under main's
runner judged nothing, D-0012), GOH_DIR and PYTHONPATH are main's, the tree is the lane's.
`--since main` is the base: main has moved onto the tip by now.

THE PAIR. Main green beside the tip's green is `green`, beside its red `broken`. Main red is run
again (green then: `flaky`), then the tip's own version runs in the same tree as its control: green
is `would_red`, red `both_red`. What gave no verdict -- a deadline, a script that did not start, a
step that does not apply here -- is `unavailable` by its reason, never green.

WHAT IT DOES NOT RUN. The tests: rust_gate.sh runs with its coverage group deferred, whose suite is
the lane's own tests under any runner. Pinned by tests/test_land_judge_steps.py.

GOH.ONEGATE: main's version of each gate step a lane changed, over the lane's tree: `choose` (the
steps whose closure moved, dropped, deleted) and `judge` (each one's outcome beside the tip's).
Invoked by `land_judge.py`.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_identity  # the lib directory, on the path from the line above
import step_closure
from land_judge_rows import Step

ROOT = Path(__file__).resolve().parent.parent

READ = (
    "unset GOH_CI_STEPS GOH_LAND_GATE; . ./.gatesrc >/dev/null 2>&1 </dev/null; "
    'printf "%s\\0%s" "${GOH_CI_STEPS-}" "${GOH_LAND_GATE-}"'
)
DIRS = "gates|checks|lib|tools|scripts|hooks|tui"
CODE = re.compile(rf"(?<![\w./$}}-])(?:\./)?((?:{DIRS})/[\w./-]+)")
SINCE = re.compile(r"--since main(?![\w./-])")
TAG = re.compile(r"^\[[^\]]*\]\s+")
STEP_MAX = "30m"  # one run of one step: twice local_ci's own ceiling, at a lane's pace
WAIT = "1m"  # for a slot: past it there is no room after all, and it is asked again
IN_SLOT = ': >"$1"; exec bash -c "$2" >"$3" 2>&1'
BUSY, DEADLINE = 3, 124  # tools/load.py: no slot within the wait; the command past its --max
NO_START = (126, 127)  # the shell found no such command, or could not run it
NOT_HERE = 78  # gates/_not_applicable.sh
GATE = "GOH_LAND_GATE"
# The release canary reads its own checkout unless told, and records its verdict where a ship looks
# for it: the judge's is told the lane's tree and given a copy of its state and of the registry.
CANARY = "scripts/release_canary.py"
STATE = "estate_audit.json"  # the name its state file has (lib/estate_registry.py)
DROP = re.compile(r"^(GIT_|GOH_LAND_|GOH_TIMINGS|GOH_TIMING_VIA$|GOH_LIVE|GOH_HELD_LOCKS$|"
                  r"GOH_GIT_ROOT$|GOH_CHANGE_BASE$|GOH_PROFILE_|GOH_GATE_CLASS$|GOH_BIN$|GOH_COV_|"
                  r"GOH_RESOLVED_)")  # fmt: skip

Run = Callable[[str, dict], "tuple[int | None, str]"]


@dataclass(frozen=True)
class Choice:
    step: str
    kind: str  # changed | same | dropped | deleted | unavailable
    reason: str = ""


def declared(root: Path) -> tuple[list[str], str]:
    """(GOH_CI_STEPS, GOH_LAND_GATE) as ROOT's .gatesrc says them; no readable list raises."""
    done = subprocess.run(["bash", "-c", READ], cwd=root, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=False, timeout=60)  # fmt: skip
    listed, _, gate = done.stdout.partition("\0")
    steps = [s.strip() for s in listed.split(":") if s.strip()]
    if done.returncode or not steps:
        raise LookupError(f"no GOH_CI_STEPS read from {root.name[:12]}'s .gatesrc")
    return steps, gate


def scripts(step: str, root: Path) -> list[str]:
    """The files of ROOT's gate code STEP names."""
    return sorted({m.group(1) for m in CODE.finditer(step) if (root / m.group(1)).is_file()})


def digest(step: str, root: Path) -> str:
    """STEP's goh half at ROOT: its closure's content, the whole content where none is placed."""
    try:
        files, why = step_closure.step_closure(step, root, root)
    except LookupError as e:
        files, why = [], str(e)
    if why:
        return f"full {gate_identity.identity(root)}"
    return f"{len(files)} {step_closure._part_hash(root, tuple(files))}"


def choose(main: Path, tip: Path, digest: Callable[[str, Path], str] = digest) -> list[Choice]:
    ours, gate = declared(main)
    theirs, their_gate = declared(tip)
    out = []
    if gate != their_gate:
        said = f"the land's gate is `{their_gate or 'the push gate'}`, main's is " \
               f"`{gate or 'the push gate'}`"  # fmt: skip
        out.append(Choice(GATE, "dropped", said))
    for step in ours:
        named = scripts(step, main)
        gone = [f for f in named if not (tip / f).is_file()]
        if gone:
            out.append(Choice(step, "deleted", f"the lane has no {', '.join(gone)}"))
        elif step not in theirs:
            now = [t for t in theirs if t not in ours and set(scripts(t, tip)) & set(named)]
            out.append(Choice(step, "dropped", "GOH_CI_STEPS has "
                              + (f"`{now[0]}` in its place" if now else "it no more")))  # fmt: skip
        else:
            try:
                moved = digest(step, main) != digest(step, tip)
            except (OSError, ValueError) as e:
                out.append(Choice(step, "unavailable", f"no closure: {e.__class__.__name__}: {e}"))
                continue
            out.append(Choice(step, "changed" if moved else "same"))
    return out


def command(step: str, main: Path, base: str, scratch: Path) -> str:
    """STEP as it runs main's code over the tree it is started in."""

    def to_main(m: re.Match) -> str:
        at = main / m.group(1)
        if not at.is_file():
            return m.group(0)
        said = shlex.quote(str(at))
        if m.group(1) == CANARY:
            said += f' --goh "$PWD" --state {shlex.quote(str(scratch / STATE))}'
        return said

    return SINCE.sub(f"--since {base}", CODE.sub(to_main, TAG.sub("", step.strip())))


def environ(root: Path, scratch: Path) -> dict[str, str]:
    """A step's environment under ROOT's code: the gate's own, with nothing a land handed down, and
    SCRATCH for its temp files (the steps leave some: a gate's runner removes them too)."""
    env = {k: v for k, v in os.environ.items() if not DROP.match(k)}
    path = [p for p in env.get("PYTHONPATH", "").split(":") if p and p != str(root)]
    return {**env, "GOH_DIR": str(root), "PYTHONPATH": ":".join([str(root), *path]),
            "GOH_PROVEN": "0", "GOH_RUST_COVERAGE": "defer", "TMPDIR": str(scratch),
            "GOH_PY": env.get("GOH_PY") or sys.executable}  # fmt: skip


def said(text: str, *hide: str) -> str:
    """What a red step says: its first error mark (the finding) and its last (the step that had
    it), else its last line."""
    for h in hide:
        text = text.replace(f"{h}/", "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    marked = [ln for ln in lines if ln.startswith("✗")]
    first, last = (marked[0], marked[-1]) if marked else ("", (lines or ["no output"])[-1])
    return last[:300] if first in ("", last) else f"{first[:200]} | {last[:160]}"


class InSlot:
    """A step run in TREE in a gate slot of its own, class `work` (tools/load.py), once ROOM returns
    (lib/land_judge_room.py): it waits holding no slot, and a slot not had within WAIT is asked for
    again after it. `ran` and `waited` are its seconds so far in the steps and in the waits."""

    def __init__(self, tree: Path, scratch: Path, room: Callable[[], None], most: str = STEP_MAX,
                 wait: str = WAIT) -> None:  # fmt: skip
        self.tree, self.room, self.most, self.wait = tree, room, most, wait
        self.mark, self.out = scratch / "started", scratch / "out"
        self.ran = self.waited = 0.0

    def waiting(self) -> None:
        t0 = time.time()
        try:
            self.room()
        finally:
            self.waited += time.time() - t0

    def __call__(self, cmd: str, env: dict) -> tuple[int | None, str]:
        while (got := self._once(cmd, env)) is None:
            pass
        return got

    def _once(self, cmd: str, env: dict) -> tuple[int | None, str] | None:
        """One ask for room and a slot, and the step run; None where no slot came."""
        self.waiting()
        self.mark.unlink(missing_ok=True)
        argv = [sys.executable, str(ROOT / "tools" / "load.py"), "--wait-max", self.wait,
                "--background-ok", "land judge", "--max", self.most, "0", "--", "bash", "-c",
                IN_SLOT, "judge", str(self.mark), cmd, str(self.out)]  # fmt: skip
        t0 = time.time()
        done = subprocess.run(argv, cwd=self.tree, env={**env, "GOH_GATE_CLASS": "work"},
                              capture_output=True, text=True, errors="replace",
                              stdin=subprocess.DEVNULL, check=False)  # fmt: skip
        if not self.mark.exists():
            self.waited += time.time() - t0
            if done.returncode == BUSY:
                return None
            return None, f"it did not start: {said(done.stdout + done.stderr)}"
        began = min(max(self.mark.stat().st_mtime, t0), time.time())
        self.waited += began - t0
        self.ran += time.time() - began
        if done.returncode == DEADLINE:
            return None, f"it passed its deadline of {self.most}"
        return done.returncode, self.out.read_text(errors="replace")


def _void(rc: int | None, text: str) -> str:
    """Why exit RC is no verdict, or ""."""
    if rc is None:
        return text
    if rc == NOT_HERE:
        return "it does not apply here (exit 78)"
    if rc in NO_START or rc < 0:
        return f"it did not run (exit {rc}): {said(text)}"
    return ""


def _one(c: Choice, main: Path, tip: Path, base: str, scratch: Path, tip_rc: int, run: Run) -> Step:
    cmd, env = command(c.step, main, base, scratch), environ(main, scratch)
    hide = (str(main), str(tip))
    note = c.reason
    rc, text = run(cmd, env)
    if void := _void(rc, text):
        # dropped and deleted are read from the two lists: they stand with no run at all
        stands = c.kind if c.kind in ("dropped", "deleted") else "unavailable"
        why = f"{note}; main's version: {void}" if note else void
        return Step(c.step, stands, why, None, tip_rc)
    if c.kind in ("dropped", "deleted"):
        why = f"{note}; main's version is {'green' if rc == 0 else 'red: ' + said(text, *hide)}"
        return Step(c.step, c.kind, why, rc, None)
    if rc == 0:
        if tip_rc:
            why = f"main's version is green over this tree, the lane's exit {tip_rc}"
            return Step(c.step, "broken", why, 0, tip_rc)
        note = "its tests are the lane's: not judged" if "gates/rust_gate.sh" in cmd else None
        return Step(c.step, "green", note, 0, 0)
    why = said(text, *hide)
    again, _ = run(cmd, env)
    if again == 0:
        return Step(c.step, "flaky", f"red, then green: {why}", rc, tip_rc)
    own, own_text = run(SINCE.sub(f"--since {base}", TAG.sub("", c.step)), environ(tip, scratch))
    if void := _void(own, own_text):
        return Step(c.step, "unavailable", f"main red: {why}; its control: {void}", rc, None)
    return Step(c.step, "both_red" if own else "would_red", why, rc, own)


def judge(choices: list[Choice], main: Path, tip: Path, base: str, scratch: Path,
          red: dict[str, int], run: Run, landed: bool = True,
          clock: Callable[[], float] = time.monotonic) -> list[Step]:  # fmt: skip
    """Each of CHOICES judged by RUN. RED: the steps the tip's own gate had red (a land not LANDED
    is judged on those alone: the rest of its gate gave no verdict to set beside main's)."""
    out = []
    for c in choices:
        if c.kind in ("same", "unavailable"):
            out.append(Step(c.step, c.kind, c.reason or None))
            continue
        if c.step == GATE:
            out.append(Step(c.step, c.kind, c.reason))
            continue
        if not landed and c.kind == "changed" and c.step not in red:
            continue
        t0 = clock()
        step = _one(c, main, tip, base, scratch, red.get(c.step, 0), run)
        step.seconds = round(clock() - t0, 2)
        out.append(step)
    return out
