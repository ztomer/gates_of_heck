"""The CI-watching judge of agent_guard.py: does a spawn prompt REQUEST that its agent wait on CI?

Split from agent_guard.py to hold its file-length cap (GOH.CIJUDGEFP, 2026-11-08). Stdlib only."""

from __future__ import annotations

import re

# Agents end at push (owner ruling 2026-11-08, GOH.ENDATPUSH): CI on the landed HEAD is the
# coordinator's `loopctl done` gate, and agents that waited on it spent 54% of their context after
# the first push (GOH.SPENDWATCH). A REQUEST aimed at the agent to wait for/watch/poll CI, run `gh
# run watch|view`, or report a CI run id/verdict/conclusion refuses the spawn. GOH.CIJUDGEFP
# (2026-11-08): the judge reads requests, not keywords. A prompt is cut into clauses (at . ; : and
# newlines, then at commas, "and", "then", "but", "before", "after"); a clause is a request only when
# it OPENS with the ask (an imperative, optionally behind "please", "then", "you must", "once
# pushed"), so a clause that merely mentions the check ("a spawn that asks an agent to watch CI",
# "refuses a report that names the CI run id") passes, and a negation covers only the clause it
# opens ("do not watch CI" is the ruling itself; "leave out nothing, report the CI run id" is a
# request, its "leave" and "nothing" belong to the clause before the comma). A negated bare verb list
# ("do not watch, poll or wait for CI") carries its negation across the commas and "or"/"and" that
# continue it. "run ./release.sh" names no CI and passes.
_LEAD = (
    r"^\W*(?:(?:then|also|and|so|but|finally|next|afterwards?|please|now|first|once\s+\w+(?:\s+\w+)?|"
    r"after\s+(?:that|pushing|the\s+push)|when\s+(?:done|pushed)|"
    r"(?:you|the\s+agent|agent)\s+(?:must|should|will|shall|need\s+to|needs\s+to|have\s+to|has\s+to|are\s+to|is\s+to)|"
    r"(?:make|be)\s+sure\s+(?:to|you)|remember\s+to|"
    r"(?:tell|ask|instruct|have|get)\s+(?:the\s+)?agent\s+to|must|should)\b[\s,]*)*"
)
_WAIT = r"(?:wait(?:s|ing)?\s+(?:for|on)|watch(?:es|ing)?|poll(?:s|ing)?)\s+(?:the\s+)?"
_GIVE = (
    r"(?:report|give|include|state|print|return|tell|paste|list|provide|record|note|send|name|add|quote|"
    r"show|say|mention|capture|cite|confirm|(?:your\s+)?(?:report|summary|answer|reply)\s+"
    r"(?:must|should|will|shall)\s+(?:include|contain|name|state|have))"
)
CI_ASK = re.compile(
    _LEAD + r"(?:" + _WAIT + r"CI\b"
    r"|(?:run|use|call|execute|invoke)\s+`?gh\s+run\s+(?:watch|view)\b"
    r"|`?gh\s+run\s+(?:watch|view)\b"
    r"|" + _GIVE + r"\b[^.;:\n]*?\bCI\s+(?:run\s+id|verdict|conclusion|result)s?\b)",
    re.IGNORECASE,
)
# GOH.SHIP (2026-11-08): a private repo's agent hands off at "goh ship started"; the pipeline it
# started (push, release, install, verify card, `loopctl done`) is watched by no turn. A request
# for the agent to run it in the foreground (`goh ship --wait`) or to wait for, watch or poll it
# is the same spend in a new place, and refuses the spawn the same way.
SHIP_ASK = re.compile(
    _LEAD + r"(?:(?:run|use|call|execute|invoke|start)\s+`?goh\s+ship\b[^.;\n]*--wait\b"
    r"|" + _WAIT + r"(?:goh\s+)?ship\b)",
    re.IGNORECASE,
)
SHIP_REFUSAL = (
    "Refused: this spawn asks the agent to run goh ship in the foreground or to wait for, watch or "
    "poll it ({ask!r}). A private repo's agent ends at \"committed, local gate green, goh ship "
    'started" (GOH.SHIP): the pipeline records the item through `loopctl done`, and a failure comes '
    "back to the coordinator as the result's failing step and log tail."
)
CI_NEGATED = re.compile(
    r"^\W*(?:(?:please|then|and|also|but|so)\s+)*(?:(?:you|the\s+agent|agent)\s+)?"
    r"(?:(?:must|should|shall|will|do|does|can|may)\s+)?"
    r"(?:not|never|don't|dont|no|without|avoid|refrain|skip|ignore)\b",
    re.IGNORECASE,
)
# What is left of a negated clause once the negator is gone: only ask verbs, i.e. a list whose
# object comes in a later clause ("do not watch, poll or wait for CI").
_BARE_VERBS = re.compile(
    r"^(?:\W|\b(?:wait(?:ing)?|watch(?:ing)?|poll(?:ing)?|for|on|or|and|report(?:ing)?|give|"
    r"include|state|print|tell|ask(?:ing)?|to|you|the|agent|not|never|do|don't|dont|no|without|"
    r"avoid|refrain|from|skip|ignore|must|should|shall|will|can|may|please|then|also|but|so|ever|under|any|circumstances|case|event|at|all)\b)*$",
    re.IGNORECASE,
)
CI_REFUSAL = (
    "Refused: this spawn asks the agent to wait for, watch or poll CI, or to report a CI run id or "
    "verdict ({ask!r}). Agents end at push (owner ruling 2026-11-08): CI on the landed HEAD is the "
    "coordinator's `loopctl done` gate. Ask for the commits and whether they are pushed and tagged."
)
_HARD = re.compile(r"(?:[.;:](?:\s|$)|\n)+")
_SOFT = re.compile(r",|\s+(?:and|or|then|but|before|after|until)\s+", re.IGNORECASE)


def _clauses(body: str):
    """(clause, negated) for every clause of the prompt, in order."""
    for segment in _HARD.split(body):
        carried = False
        pos = 0
        for m in [*_SOFT.finditer(segment), None]:
            end = m.start() if m else len(segment)
            clause = segment[pos:end]
            joiner = m.group(0).strip(", ").lower() if m else ""
            pos = m.end() if m else len(segment)
            negated = bool(CI_NEGATED.match(clause)) or carried
            yield clause, negated
            # a negation carries over a continuing list, never over "then"/"but"/"before"/"after"
            bare = bool(_BARE_VERBS.match(clause))
            carried = negated and bare and joiner in {"", "and", "or"}


def judge_ci(prompt: str, contract: str = "") -> str | None:
    """The refusal for a prompt that asks its agent to wait on CI, or None.

    `contract` is the report contract the guard appends itself; it is not the caller's request."""
    body = prompt.replace(contract, "") if contract else prompt
    for clause, negated in _clauses(body):
        if negated:
            continue
        if m := CI_ASK.search(clause):
            return CI_REFUSAL.format(ask=m.group(0).strip())
        if m := SHIP_ASK.search(clause):
            return SHIP_REFUSAL.format(ask=m.group(0).strip())
    return None
