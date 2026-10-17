"""Probe rows for the display seam's DECLARED-HARNESS tiers, kept beside the probe that runs them.

A Python harness that drives the real screen declares itself by CALLING the policy's
`live_helper`. A policy may name a second tier, `ingress_helper` (8e44a2d, from ZeroThunder 1.9):
a harness that drives the real INPUT session calls that one instead. Both tiers are held to the
same rules -- a call, not a mention, with a reason that is not a placeholder -- and the second
tier is opt-in: its default is "", and a policy that never named it must get no exemption from it.

Each row is (label, files, policy overrides, WANTED EXIT CODE), the shape of
`_display_seam_probe.CASES`; the probe writes the seam file into every fixture itself. They live
here so the probe stays under the 500-line cap, and `check_display_seam.py --probe` runs them.
"""

from __future__ import annotations

LIVE = "require_live_screen"
INGRESS = "require_live_session"
CAPTURE = 'import subprocess\nsubprocess.run(["screencapture", "-x", "a.png"])\n'


def _declared(helper, *args):
    """A harness that declares itself by calling `helper(*args)`, then executes a live command."""
    quoted = ", ".join(f'"{a}"' for a in args)
    return f"from p import {helper}\n{helper}({quoted})\n{CAPTURE}"


TIER_CASES = (
    (
        "a declared live-tier harness with a real reason is accepted",
        {"tests/live.py": _declared(LIVE, "x.py", "drives the real compositor via SkyLight")},
        {"scan": ["tests"]},
        0,
    ),
    (
        "a live declaration with NO reason is a violation",
        {"tests/live.py": _declared(LIVE, "x.py")},
        {},
        1,
    ),
    (
        "a PLACEHOLDER live reason is a violation",
        {"tests/live.py": f'from p import {LIVE}\n{LIVE}("x.py", "needs the real screen")\n'},
        {},
        1,
    ),
    (
        "an INGRESS-tier harness is accepted when the policy names its helper",
        {"tests/ing.py": _declared(INGRESS, "x.py", "posts real key events through the session")},
        {"scan": ["tests"], "ingress_helper": INGRESS},
        0,
    ),
    (
        # The default is "". A consumer that never named the second tier must not inherit it.
        "the SAME ingress declaration buys nothing when the policy names no ingress helper",
        {"tests/ing.py": _declared(INGRESS, "x.py", "posts real key events through the session")},
        {"scan": ["tests"]},
        1,
    ),
    (
        "a PLACEHOLDER ingress reason is a violation",
        {"tests/ing.py": _declared(INGRESS, "x.py", "needs the real screen")},
        {"ingress_helper": INGRESS},
        1,
    ),
    (
        # With no second tier, the helper pattern must not gain an EMPTY alternative: `(?:live|)`
        # makes any line opening with a two-string tuple read as a declaration with a reason.
        "with no ingress helper named, a bare two-string tuple declares nothing",
        {"tests/t.py": f'("x.py", "a reason long enough to pass every length check")\n{CAPTURE}'},
        {},
        1,
    ),
)
