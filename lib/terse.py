"""lib/terse.py: the ONE output contract of a lane-facing goh command's red (GOH.TERSEOUT).

Every line a goh tool prints is re-read on every later call of the lane that ran it (cache reads
are the largest share of a lane's token cost, LOOP.LANECONTEXT): GOH-TINY's lane reached ~400k
context over 308 calls. `goh land --wait` printed a red record's whole `why` (a pre-step's excerpt,
up to 400 lines) and the log's last 30 lines; the foreground pre-step streamed every pytest line;
`goh land --check` printed each refused guard's last 25 lines. By default each now prints

    the verdict line
      each failing test id: its first assertion line     (pytest's FAILED lines, its E lines)
      ... or, with none, the log's marked lines (✗, ERROR) and then its last lines
    log PATH (--full prints it whole)

under LIMIT lines in all. `--full` (GOH_FULL=1, which the flag sets) prints the log whole.
`next_gap` is the wait's heartbeat: an unchanged state said every 60 s is 30 lines over a 30 min
land, so the gap doubles (full keeps it fixed). Pinned by tests/test_terse.py.
"""

from __future__ import annotations

import os
import re
import sys

LIMIT = 30  # lines of a whole red report, the verdict and the log path included
WIDE = 240  # characters per line kept
FAILED = re.compile(r"^(?:FAILED|ERROR) (tests/\S+?\.py(?:::\S+)?)(?: - (.*))?$")
SECTION = re.compile(r"^_{3,} (.+?) _{3,}$")
MARKED = re.compile(r"✗|\bERROR\b|\bFAILED\b|^error[:\[]|Traceback")


def full() -> bool:
    """`--full`, or GOH_FULL=1: the whole log, never the summary."""
    return os.environ.get("GOH_FULL", "") not in ("", "0")


def next_gap(gap: float) -> float:
    """The wait's next heartbeat gap after GAP: doubled, unless --full."""
    return gap if full() else gap * 2


def _first_assertions(log: list[str]) -> dict[str, str]:
    """Each pytest failure section's head (`test_x[a]`, `TestC.test_x`) -> its first E line."""
    out: dict[str, str] = {}
    head = ""
    for line in log:
        if m := SECTION.match(line):
            head = m[1]
        elif head and line.startswith("E ") and head not in out:
            out[head] = line[1:].strip()
    return out


def failures(log: str) -> list[tuple[str, str]]:
    """(test id, its first assertion line) for each test the log names FAILED or ERROR, once."""
    lines = log.splitlines()
    first = _first_assertions(lines)
    seen: dict[str, str] = {}
    for line in lines:
        if (m := FAILED.match(line.strip())) and m[1] not in seen:
            leaf = m[1].split("::", 1)[-1].replace("::", ".")
            seen[m[1]] = first.get(leaf) or (m[2] or "").strip()
    return list(seen.items())


def summary(log: str, room: int) -> list[str]:
    """At most ROOM lines naming what failed in LOG (module doc), each indented two spaces."""
    room = max(room, 1)
    if found := failures(log):
        shown = found if len(found) <= room else found[: room - 1]
        out = [f"  {node}: {why}"[:WIDE] if why else f"  {node}"[:WIDE] for node, why in shown]
        if len(found) > len(shown):
            out.append(f"  ... {len(found) - len(shown)} more failing tests (--full lists them)")
        return out
    lines = [ln.rstrip() for ln in log.splitlines() if ln.strip()]
    marked = [ln for ln in lines if MARKED.search(ln)][-(room // 2 or 1) :]
    tail = [ln for ln in lines[-room:] if ln not in marked][-(room - len(marked)) :]
    tail = tail if room > len(marked) else []
    return [f"  {ln}"[:WIDE] for ln in marked + tail]


def report(verdict: str, log: str, path: str, limit: int = LIMIT) -> list[str]:
    """VERDICT, then what failed in LOG, then PATH: under LIMIT lines, or LOG whole with --full."""
    if full():
        return [verdict, *(f"  {ln}" for ln in log.splitlines()), f"log {path}"]
    return [verdict, *summary(log, limit - 3), f"log {path} (--full prints it whole)"]


def under(head: str, out: str, room: int) -> list[str]:
    """HEAD, then what failed in OUT in at most ROOM lines (a refused guard of `goh land
    --check`: a 25-line tail each was 50+ lines for two); OUT whole with --full."""
    return [head, *(out.rstrip().splitlines() if full() else summary(out, room))]


def say_refused(say, text: str, suffix: str) -> None:
    """A refusal's first line through SAY with SUFFIX, then what failed in the rest (`message`)."""
    first, *rest = message(text)
    say(f"{first}{suffix}")
    for line in rest:
        print(line, file=sys.stderr)


def message(text: str, limit: int = LIMIT) -> list[str]:
    """A multi-line refusal (a pre-step's Red: its why, then its excerpt): the first line, then
    what failed in the rest, under LIMIT lines; whole with --full."""
    first, *rest = text.splitlines() or [""]
    if full() or not rest:
        return [first, *rest]
    return [first, *summary("\n".join(rest), limit - 1)]
