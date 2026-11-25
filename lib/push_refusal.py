"""lib/push_refusal.py -- what a consumer's push gate refused at, read from its log (GOH.PINSWEEP).

    refusal(text)            Refusal(judged, steps, said): what a failed push's log says

WHY. The reason the ledger kept was the log's last three lines, `load: spinners stopped, registry
cleared` among them.

It imports no reader of goh's own tree (lib/push_hold.py does): lib/consumer_pins.py and all that
reads it, `goh ship` among them, would reach the step closure's key words by it, and every test of
theirs would be one that runs any subcommand of the binary (lib/binary_runners.py).
Pinned by tests/test_push_hold.py.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fail_lines  # lib/, beside this file

SAID, WIDE = 4, 160  # a reason is this many of the log's fault lines, each at most this wide
_COUNTED = re.compile(r"^✗ \d+ of \d+ steps? failed:")
_NUMBERED = re.compile(r"^✗ \[\d+/\d+\] FAILED \([^)]*\): (?P<step>.+)$")
_STEP = re.compile(r"^ {2}(?P<step>\S.*?)(?: \([^()]*\))?$")
DIED = re.compile(r"^✗ (?P<gate>[\w-]+): (?P<label>.+?) failed(?: \((?:native|exit|command)\b.*)?$"
                   )  # fmt: skip
_NOISE = re.compile(r"^✗\s*$|^✗ +full output:|^✗ \d+ of \d+ steps? failed:|"
                    r"^✗ \[\d+/\d+\] FAILED ")  # fmt: skip
_FAULT = re.compile(fail_lines.DEFAULT_PATTERN)
_STEP_SAID = re.compile(r"\(exit status \d+\)$")  # the runner's own list of the failed steps
_GATED = re.compile(r"^✗ pre-push: the gate failed")
_REFUSED = re.compile(r"^error: failed to push some refs")
# The push failed and no gate said so: the commit moved under it, or the remote or the network did.
_UNJUDGED = re.compile(r"HEAD moved while it was being gated|\[(?:remote )?rejected\]|^fatal: ",
                       re.MULTILINE)  # fmt: skip


@dataclass(frozen=True)
class Refusal:
    judged: bool  # a gate ran and said no
    steps: tuple[str, ...]  # the ones it named as failed
    said: str  # the steps and their fault lines, for the ledger


def _steps(lines: list[str]) -> list[str]:
    """The failed steps as the runner lists them, else each `FAILED` row's, else each gate's own
    last word (`structural: LABEL failed`)."""
    at = next((i for i in range(len(lines) - 1, -1, -1) if _COUNTED.match(lines[i])), None)
    if at is not None:
        listed = []
        for line in lines[at + 1 :]:
            if line.startswith("✗ ") and line.strip("✗ "):
                break
            if m := _STEP.match(line):
                listed.append(m["step"])
        if listed:
            return list(dict.fromkeys(listed))
    for form, of in ((_NUMBERED, "{step}"), (DIED, "{gate}: {label}")):
        if got := [of.format(**m.groupdict()) for m in map(form.match, lines) if m]:
            return list(dict.fromkeys(got))
    return []


def _faults(lines: list[str]) -> list[str]:
    """The lines that say what failed: the `✗` ones that are no frame, a died step's by its label
    alone; with none, the last lines the gate printed."""
    out = []
    for line in lines:
        if line.startswith("✗") and not _NOISE.match(line):
            m = DIED.match(line)
            out.append(f"{m['gate']}: {m['label']} failed" if m else line.lstrip("✗ ").strip())
    if out := [x for x in dict.fromkeys(out) if x]:
        return out
    plain = [x.strip() for x in lines if x.strip("─ ") and x[0] not in "✗→·✓"]
    named = [x for x in plain if _FAULT.search(x) and not _STEP_SAID.search(x)]
    return (named or plain)[-SAID:]


def refusal(text: str) -> Refusal:
    lines = [fail_lines.ANSI.sub("", ln).rstrip() for ln in text.splitlines()]
    judged = any(_GATED.match(ln) or _REFUSED.match(ln) for ln in lines) and not (
        _UNJUDGED.search("\n".join(lines))
    )  # a log cut off before either: nothing judged it
    end = next((i for i, ln in enumerate(lines) if _GATED.match(ln) or _REFUSED.match(ln)), None)
    if end is None:
        end = next((i for i, ln in enumerate(lines) if ln.startswith("load: ")), len(lines))
    elif not judged and not any(ln.startswith("✗") for ln in lines[:end]):
        end = len(lines)  # git's own words come after
    steps, faults = _steps(lines[:end]), _faults(lines[:end])
    if not judged:
        return Refusal(False, (), " | ".join(x[:WIDE] for x in faults[:SAID]))
    faults = [f for f in faults if not any(f == f"{s} failed" for s in steps)]
    said = " | ".join([*steps, *(x[:WIDE] for x in faults[:SAID])])
    return Refusal(True, tuple(steps), said)
