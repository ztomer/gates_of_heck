#!/usr/bin/env python3
"""Shared rule: an escape-hatch marker must carry an actual justification.

Every gate that offers an escape hatch has a marker that silences it -- `screen-ok:`,
`input-ok:`, `token-ok:`, `stub-ok`. The house rule is that each one is an exception you
ARGUE for. Checkers that match only the marker STRING accept a bare tag with nothing after
it, which silences the gate completely: append the tag to a real violation and the gate
passes.

WHY IT IS HERE AND NOT IN A CONSUMER. `games/ZeroThunder/tools/marker_reason.py` held this
rule, eight of that repo's gates imported it, and the seam gate that needed it was moved
into this repo (SUPERSOTA R5: a checker one repo needs lives here). Copying the rule in would
have left two copies of a rule about copies, so this is the rule and that file is now a
re-export of it.

THE BAR IS "YOU SAID SOMETHING REAL", NOT A LENGTH. A 25-character bar is right for a
whole-harness declaration and absurd for an inline per-line marker, where "attended debug
program" is honest and complete -- a length rule there just trains people to write filler,
which makes the next reader trust reasons less.

AND THE LIMIT IS WORTH STATING PLAINLY. This gates INTENT, not EFFECT. It stops a lazy
bypass; it cannot stop a plausible one, because a fabricated reason is the same KIND of
sentence as a true one. Only a shrink-only ceiling on the total catches that.

    _marker_reason.py            # the cases
"""

from __future__ import annotations

import re

PLACEHOLDER = re.compile(
    r"^[\s:—-]*(tbd|todo|n/?a|because|obvious|fine|ok|okay|yes|no|see above|why not|needed"
    r"|required|intentional|deliberate|on purpose|by design)\b\.?\s*$",
    re.I,
)

SEPARATOR = re.compile(r"^[\s:—\-]+")

CASES = (
    ("x  // stub-ok", "// stub-ok", False, "bare"),
    ("x  // stub-ok: ", "// stub-ok", False, "colon then nothing"),
    ("x  // stub-ok: tbd", "// stub-ok", False, "placeholder"),
    ("x  // stub-ok: by design", "// stub-ok", False, "placeholder phrase"),
    ("x  // stub-ok: smoke test, asserts nothing on purpose", "// stub-ok", True, "real"),
    ("x  // stub-ok: defines the overscan", "// stub-ok", True, "real and terse"),
    ("x  // other-marker", "// stub-ok", False, "marker absent"),
    ("# screen-ok: attended debug program", "screen-ok:", True, "python comment form"),
    ("// screen-ok: drives the live compositor via SkyLight", "screen-ok:", True, "swift comment"),
    ("// screen-ok: because", "screen-ok:", False, "placeholder, this rule's bane"),
    ("// input-ok: measured live taps during a run", "input-ok:", True, "the second marker"),
)


def reason_after(line: str, marker: str) -> str | None:
    """Text following `marker` on `line`, or None if the marker is absent.

    Leading separator punctuation is stripped BEFORE the caller tests for emptiness, because
    `// stub-ok:` with nothing after it would otherwise leave a bare ":" -- which is not the
    empty string, so it passed a naive truthiness test.
    """
    i = line.find(marker)
    if i < 0:
        return None
    return SEPARATOR.sub("", line[i + len(marker) :]).strip()


def is_justified(line: str, marker: str) -> bool:
    """True if `marker` appears on `line` AND carries a real justification."""
    why = reason_after(line, marker)
    if why is None:
        return False
    return bool(why) and not PLACEHOLDER.match(why)


def selftest() -> int:
    """Run the cases above. Exits non-zero on any wrong verdict."""
    bad = [why for line, marker, want, why in CASES if is_justified(line, marker) != want]
    if bad:
        print(f"[marker-reason] SELFTEST FAILED: {', '.join(bad)}")
        return 1
    print(f"[marker-reason] selftest OK - {len(CASES)} cases")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(selftest())
