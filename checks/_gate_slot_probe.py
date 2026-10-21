"""The kit behind the gate-slot race probe: the instrumented writer, and the trial that reads it.

    tools/gate_slot_race_probe.py     # the measurement (this kit, with a CLI)
    tests/test_gate_slot.py            # the regression test (this kit, with an assertion)

WHY IT EXISTS (E39, 2026-10-20). The gate-slot wait report is TWO writes: `lib/gate_slot.py`
prints the header ("all N gate slots are held") and then, for each slot, the line that names its
holder ("slot-0: repo (pid, since)"). A reader that polls the output and waits for the HEADER
can read the file in the window between the two writes, and see a state that does not yet carry
the line it is about to assert on. A test that waits for the header and then requires the
holder's line is a flake: it passes until a poll lands in that window.

THE WINDOW IS REAL AND SMALL. Measured on a quiet box (2026-10-21): ~50 us between the two
writes (the holder's line is formatted from the slot record, a file read, between them). At the
suite's 50 ms poll interval that is a flake; under load the writer can be descheduled inside the
window and it is a failure. The box's own words: "a race that cannot be red-proven is not
fixable in a release".

THE INSTRUMENTED WRITER. `instrumented_copy()` builds a COPY of lib/ and tui/ whose writer
pauses between the two writes (and stamps when each happened). The working tree is never
touched. With the pause the window is wide enough to be caught EVERY time instead of once in a
while -- which is how the race is proved DETERMINISTICALLY, and how the fixed reading is proved
to be independent of where the writer's writes fall.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

# The header the suite's helper waits for, and the holder line it asserts on.
HEADER = "all 1 gate slots are held"
HOLDER_LINE = "slot-0:"

# A gate: takes a slot, says so, then holds it until its release file appears.
GATE = """
set -euo pipefail
GOH="{goh}"; . "$GOH/tui/lib.sh"; . "$GOH/lib/gate_slot.sh"
trap gate_slot_release EXIT
gate_slot_acquire "{name}"
echo "TAKEN pid=$$"
while [ ! -f "{release}" ]; do sleep 0.05; done
echo "DONE"
"""

# The seam the probe pauses at: the holder lines are printed after the header, one print()
# each, so a reader can land between them.
PAUSE_SEAM = """            for i in range(slots):
                step(describe(slot_path(directory, i), now))
"""
PAUSE_PATCH = (
    """            time.sleep(float(os.environ.get("GOH_PROBE_PAUSE_S", "0") or 0))
"""
    + PAUSE_SEAM
)

# The seam the probe measures: the two writes, stamped from inside the print primitives, so
# the measurement does not add I/O to the window it measures.
TUI_IMPORT_SEAM = "import os\nimport sys\n"
TUI_IMPORT_PATCH = "import atexit\nimport os\nimport signal\nimport sys\nimport time\n"
TUI_INFO_SEAM = """def info(message: str) -> None:
    print(f"{_C_GRAY}{ICON_START}{_C_RESET} {_C_DIM}{message}{_C_RESET}", flush=True)
"""
TUI_INFO_PATCH = TUI_INFO_SEAM + '    _probe_mark("info", message)\n'
TUI_STEP_SEAM = """    print(f"{_C_DIM}{ICON_STEP} {message}{_C_RESET}", flush=True)
"""
TUI_STEP_PATCH = TUI_STEP_SEAM + '    _probe_mark("step", message)\n'
TUI_STAMP_TAIL = '''

_PROBE_MARKS: list[tuple[str, float, str]] = []


def _probe_mark(kind: str, message: str) -> None:
    """The probe's seam: when each write happened, to the microsecond."""
    _PROBE_MARKS.append((kind, time.perf_counter(), message))


def _probe_dump() -> None:
    path = os.environ.get("GOH_PROBE_STAMP_FILE")
    if not path:
        return
    with open(f"{path}.{os.getpid()}", "a", encoding="utf-8") as f:
        for kind, at, message in _PROBE_MARKS:
            f.write(f"{kind}\\t{at:.9f}\\t{message}\\n")


def _probe_dumped_exit(signum, frame):
    """The holder is released with a SIGTERM, which skips atexit: dump before it dies."""
    _probe_dump()
    os._exit(0)


atexit.register(_probe_dump)
signal.signal(signal.SIGTERM, _probe_dumped_exit)
'''


def instrumented_copy(root: Path, dest: Path, pause: float = 0.0) -> Path:
    """A copy of lib/ and tui/ that stamps the writer's writes (and can pause between them).

    The working tree is never touched: the copy is what runs. The stamp says when each write
    happened; the pause makes the window wide enough to be caught every time instead of once
    in a while, which is how the race is proved DETERMINISTICALLY rather than by repetition.

    The copy has no gates/ beside it, so the C4 re-exec (`gates/_from_head.py`) does not fire
    and the copy's own writer is the one that runs.
    """
    for name in ("lib", "tui"):
        shutil.copytree(root / name, dest / name)
    helper = dest / "lib" / "gate_slot.py"
    text = helper.read_text(encoding="utf-8")
    if pause > 0:
        if PAUSE_SEAM not in text:
            raise RuntimeError(
                "the probe cannot instrument this tree: lib/gate_slot.py no longer prints the"
                f" holder lines where the probe expects them (looked for {PAUSE_SEAM!r})"
            )
        text = text.replace(PAUSE_SEAM, PAUSE_PATCH, 1)
    helper.write_text(text, encoding="utf-8")

    tui = dest / "tui" / "lib.py"
    text = tui.read_text(encoding="utf-8")
    for seam, patch in (
        (TUI_IMPORT_SEAM, TUI_IMPORT_PATCH),
        (TUI_INFO_SEAM, TUI_INFO_PATCH),
        (TUI_STEP_SEAM, TUI_STEP_PATCH),
    ):
        if seam not in text:
            raise RuntimeError(f"the probe cannot instrument this tree: tui/lib.py has no {seam!r}")
        text = text.replace(seam, patch, 1)
    tui.write_text(text + TUI_STAMP_TAIL, encoding="utf-8")
    return dest


def window_of(stamps: Path) -> list[float]:
    """The writer's own gap between the header write and the holder line, in microseconds.

    Read from the stamps the instrumented print primitives left: the header's `info` and the
    holder line's `step`, in that order. This is the window a reader can land in, measured
    where it happens rather than inferred from how often it was caught.
    """
    out: list[float] = []
    for path in sorted(stamps.parent.glob(stamps.name + "*")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        marks = [ln.split("\t", 2) for ln in lines if ln.count("\t") >= 2]
        for i, (kind, at, message) in enumerate(marks):
            if kind != "info" or not message.startswith(HEADER):
                continue
            for kind2, at2, _ in marks[i + 1 :]:
                if kind2 == "step":
                    out.append((float(at2) - float(at)) * 1e6)
                    break
    return out


class Trial:
    """One holder/waiter pair, and what the reader saw of the waiter's output.

    The reader's side is the same shape as the suite's `Gate` helper (`wait_for(needle)`
    returns the text that carries the needle), so the reading the regression test exercises
    is the reading the suite uses.
    """

    def __init__(
        self,
        root: Path,
        work: Path,
        poll: float = 0.001,
        holder_name: str = "holder",
        pause: float = 0.0,
    ) -> None:
        self.work = work
        self.poll = poll
        self.holder_name = holder_name
        self.stamps = work / "stamps.tsv"
        self.env = dict(
            os.environ,
            GOH_DIR=str(root),
            GOH_LIVE="1",
            NO_COLOR="1",
            GOH_GATE_SLOT_DIR=str(work / "slots"),
            GOH_GATE_SLOTS="1",
            GOH_PROBE_STAMP_FILE=str(self.stamps),
            GOH_PROBE_PAUSE_S=str(pause),
        )
        self.procs: list[subprocess.Popen] = []
        self.releases: list[Path] = []
        self.holder_log = work / f"{holder_name}.out"
        self.waiter_log = work / "waiter.out"
        self.work.mkdir(parents=True, exist_ok=True)

    def _start(self, name: str) -> Path:
        log = self.work / f"{name}.out"
        release = self.work / f"rel-{name}"
        self.releases.append(release)
        script = GATE.format(goh=self.env["GOH_DIR"], name=name, release=release)
        with open(log, "w") as out:
            self.procs.append(
                subprocess.Popen(
                    ["bash", "-c", script],
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    env=self.env,
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                )
            )
        return log

    def read(self, log: Path) -> str:
        try:
            return log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def start(self) -> None:
        """The holder takes the slot, then the waiter waits for one."""
        self._start(self.holder_name)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            text = self.read(self.holder_log)
            if "TAKEN" in text:
                break
            time.sleep(0.01)
        else:
            raise RuntimeError("the probe's holder never took a slot")
        self._start("waiter")

    def holder_pid(self) -> int:
        """The holder's shell pid, read from the log the holder wrote."""
        text = self.read(self.holder_log)
        return int(text.split("TAKEN pid=", 1)[1].split()[0])

    def wait_for(self, needle: str, seconds: float = 20) -> str:
        """The text that carries `needle` -- the suite's helper's shape, on the kit's writer."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            text = self.read(self.waiter_log)
            if needle in text:
                return text
            time.sleep(self.poll)
        raise AssertionError(f"{needle!r} not seen; got {self.read(self.waiter_log)!r}")

    def header_without_line(self, seconds: float = 10.0) -> bool:
        """Whether a read saw the header and NOT the holder's line: the intermediate state.

        The reader polls as fast as it can; the writer's window is what decides whether it
        can land inside it. With the instrumented pause it lands there every time.
        """
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            text = self.read(self.waiter_log)
            if HEADER in text:
                return HOLDER_LINE not in text
            time.sleep(0.001)
        return False

    def run(self) -> tuple[int, int, int, int]:
        """(polls, sightings, failures_current, failures_fixed).

        A trial FAILS for the CURRENT shape when the first read that saw the header did not
        carry the holder's line: that is the read the current test shape treats as final. It
        FAILS for the FIXED shape when the first read that carried the needle -- the line the
        test waits for -- did not carry every substring the test asserts on.
        """
        self.start()
        holder_pid = self.holder_pid()

        # The needle the fixed shape waits for: the holder's line, up to its stamp.
        needle = f"{HOLDER_LINE} {self.holder_name} (pid {holder_pid}, since "
        asserted = ["slot-0", self.holder_name, f"pid {holder_pid}", "since "]

        polls = sightings = failures_current = failures_fixed = 0
        first_header = True
        fixed_text = None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            text = self.read(self.waiter_log)
            polls += 1
            if HEADER in text:
                if HOLDER_LINE not in text:
                    sightings += 1
                    if first_header:
                        failures_current += 1
                first_header = False
            if needle in text and fixed_text is None:
                fixed_text = text
            if fixed_text is not None:
                break
            time.sleep(self.poll)
        else:
            raise RuntimeError("the probe's waiter never reported")
        if fixed_text is None or not all(a in fixed_text for a in asserted):
            failures_fixed += 1
        return polls, sightings, failures_current, failures_fixed

    def close(self) -> None:
        for release in self.releases:
            release.touch()
        for proc in self.procs:
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                proc.wait(timeout=30)
