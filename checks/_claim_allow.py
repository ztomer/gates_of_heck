"""`claim_derivation_allow.json` -- the exemption list, and what it excuses.

Split out of `check_claim_derivation.py` at the 500-line cap, and the cut is at a
real boundary rather than an arbitrary one: the ratchet has no other caller, it
shares nothing with the grammar except the shape of the tuples it is handed, and
the rule it encodes -- **an entry that matches nothing FAILS** -- is the whole
reason the file exists and deserves its own docstring where a reader will find it.

    ```
    {"entries": [{"path": "docs/plan.md",
                  "claim": "`70` gates in `tests/e2e/verify.py`",
                  "status": "unreviewed",
                  "reason": "seeded 2026-10-04; not yet examined"}]}
    ```

(Fenced, because that `claim` value is a real claim and the file is scanned like
any other text: a gate's documentation is where its own syntax lives, and reading
it would make the documentation a finding about this repository.)

Each entry excuses ONE claim in ONE file, matched on the claim as written with all
whitespace collapsed -- so a `ruff format` cannot revoke an exemption, which is the
same reason `check_no_kill_by_name` collapses whitespace. An exemption a routine
formatter can take away is not an exemption.

Three properties, each a decision:

* **A STALE entry fails.** A claim that got fixed must take its exemption with it.
  A list that can only grow is permission, and `check_empty_scope`'s own docstring
  is the argument: a population that drops to zero reads as every ceiling met.
* **A DUPLICATE entry is stale.** The first of two identical entries takes every
  match, so the second excuses nothing -- and an entry that excuses nothing is
  permission nobody claimed.
* **Every entry needs a reason**, and `status` keeps two populations apart.
  `legitimate` is a decision whose reason is the argument someone can challenge;
  `unreviewed` is debt, found when the gate was seeded and not yet examined. An
  entry with no status is unreviewed, and every run reports the count so a list of
  debt never reads as a clean list.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import content_bytes

ALLOW_FILE = "claim_derivation_allow.json"
STATUSES = ("legitimate", "unreviewed")


def key(text: str) -> str:
    """The comparison key for an allowlist entry: the claim with ALL whitespace
    removed.

    Collapsing, not matching verbatim, for the reason the module docstring gives:
    an exemption a routine `ruff format` can revoke is not an exemption, and a
    formatter is not going to be told to leave source alone.
    """
    return "".join(text.split())


def load(root: str, staged: bool) -> tuple[list[dict], list[str]]:
    """(entries, problems). An absent file is an empty list; a malformed one is a
    problem, and a problem is exit 1 rather than a pass -- an exemption list this
    gate could not read is an exemption list silently applying nothing."""
    blob = content_bytes(root, ALLOW_FILE, staged=staged)
    if blob is None:
        return [], []
    try:
        data = json.loads(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return [], [f"{ALLOW_FILE} is not valid JSON: {exc}"]
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return [], [f'{ALLOW_FILE} must be {{"entries": [...]}}']
    problems = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(k), str) and entry[k].strip() for k in ("path", "claim", "reason")
        ):
            problems.append(f"{ALLOW_FILE} entry {i} needs a non-empty path, claim and reason")
        elif entry.get("status", "unreviewed") not in STATUSES:
            problems.append(f"{ALLOW_FILE} entry {i}: status must be one of {', '.join(STATUSES)}")
    return entries, problems


def judge(scanned, entries, files_scanned):
    """(findings, stale_entries, used_indices, excused).

    A finding is `(rel, claim, kind, message, command, value)`. `command` and
    `value` are None for an UNRESOLVED claim -- there is nothing to run when the
    tree could not answer, and pretending otherwise would print a command that
    prints nothing.
    """
    used, findings, excused = set(), [], 0
    for rel, claim, value, detail in scanned:
        match = next(
            (
                i
                for i, e in enumerate(entries)
                if e.get("path") == rel and key(e.get("claim", "")) == key(claim.text)
            ),
            None,
        )
        if match is not None:
            used.add(match)
            excused += 1
            continue
        if value is None:
            findings.append((rel, claim, "UNRESOLVED", detail, None, None))
        elif value != claim.number:
            findings.append(
                (
                    rel,
                    claim,
                    "STALE",
                    f"claims {claim.number}, the tree says {value}",
                    detail,
                    value,
                )
            )
    stale = [e for i, e in enumerate(entries) if i not in used and e.get("path") in files_scanned]
    return findings, stale, used, excused
