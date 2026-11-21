#!/usr/bin/env python3
"""`goh brief`: the header lines a subagent spawn needs, generated from the rules, never guessed.

55 spawn refusals in the 7 days to 2026-11-21 were brief headers written by trial -- no effort,
no model, general-purpose for write work, unknown tags, a tier the active profile makes `never`,
the cheap opus tier without one of its tags, a missing `Pool:` line (GOH.BRIEFHEADER). This
prints them
from `model_choice.json` read through `model_choice.load()`, so the active profile and the budget
guard's drop are the spawn guard's own (`model_choice.active_profile`), never a copy:

    goh brief --work write|read --tags T,.. [--owner-asked WORDS] [--repo R]

prints `Model-choice: <model>@<effort> because <tags>`, a `Pool:` line when the tier is pool work,
an `Agent-type:` line when a heavy type is asked for with its reason, `Owner-asked:` when given,
and the Agent call's model, effort and subagent_type (`--json` for the same as data).

The spawn guard (agent_guard.py) calls `corrected()` on a refused prompt, so its refusal carries
the header that would have passed (tests/test_goh_brief.py drives every refusal shape through it).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_choice  # the rules file's one reader, beside this script

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tui.lib import noun  # the count meets its noun (GOH.HEDGEOWN)

# Cheapest first: a spawn takes the first tier that accepts its tags. Tier NAMES, not classes: the
# classes in each tier are the rules file's (tests/test_model_choice_rules.py).
TIER_ORDER = ("lookup", "cheap", "opus", "escalation", "owner")
# The tag a spawn with no usable tag gets, by work: the honest "no mechanical check claimed".
DEFAULT_TAG = {"write": "unchecked", "read": "read-only"}
HEADER_KEYS = ("Model-choice", "Pool", "Agent-type", "Owner-asked")
HEADER_LINE = re.compile(rf"^[ \t>*-]*(?:{'|'.join(HEADER_KEYS)}):.*\n?", re.MULTILINE)


class BriefError(ValueError):
    """No header can be generated for these arguments; the message says why and what to pass."""


@dataclass
class Brief:
    model: str
    effort: str
    subagent_type: str
    tags: list[str]
    lines: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def call(self) -> str:
        fields = f"model={self.model} effort={self.effort} subagent_type={self.subagent_type}"
        return f"Agent call: {fields}"

    def text(self) -> str:
        return "\n".join([*self.lines, self.call(), *(f"Note: {n}" for n in self.notes)])

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "effort": self.effort,
            "subagent_type": self.subagent_type,
            "tags": self.tags,
            "header": self.lines,
            "notes": self.notes,
        }


def _candidates(rules: model_choice.Rules, work: str) -> list[tuple[str, str, dict[str, Any]]]:
    """Every (family, effort, rule) a spawn may take for this work, cheapest first."""
    read_only = set(rules.lean.get("read_only_tiers") or [])
    out = []
    for fam in rules.families:
        for eff in rules.efforts:
            rule = rules.rule(fam, eff)
            if rule is None or rule["tier"] not in TIER_ORDER:
                continue
            if work == "write" and rule["tier"] in read_only:
                continue
            out.append((fam, eff, rule))
    out_price = {f: (rules.price(f) or {}).get("output", 0) for f in rules.families}
    return sorted(
        out,
        key=lambda c: (
            TIER_ORDER.index(c[2]["tier"]),
            "*" in _key(rules, *c[:2]),
            rules.efforts.index(c[1]),
            out_price[c[0]],
        ),
    )


def _key(rules: model_choice.Rules, fam: str, eff: str) -> str:
    return next(k for k in (f"{fam}@{eff}", f"*@{eff}", f"{fam}@*") if k in rules.tiers)


def _missing(rule: dict[str, Any], tags: set[str]) -> set[str] | None:
    """The fewest tags to add for `rule` to accept, when the spawn declared part of a set it
    names (closest to what it meant); None when it declared none of them."""
    sets = [set(rule["tags"])] if rule["need"] == "all" else []
    sets += [set(rule["also_all"])] if rule.get("also_all") else []
    partial = [s - tags for s in sets if s & tags]
    return min(partial, key=len) if partial else None


def resolve(
    rules: model_choice.Rules,
    work: str,
    tags: list[str],
    model: str | None = None,
    effort: str | None = None,
) -> tuple[str, str, list[str], list[str]]:
    """(family, effort, the tags to declare, notes) for this work: the cheapest class the active
    profile's tiers accept. Unknown tags are dropped; a tag set nothing accepts is completed."""
    known = list(dict.fromkeys(t for t in tags if t in rules.tags))
    notes = []
    if dropped := [t for t in tags if t not in rules.tags]:
        notes.append(f"dropped unknown {noun(len(dropped), 'tag')} {', '.join(dropped)}")
    cands = [
        c
        for c in _candidates(rules, work)
        if (model is None or c[0] == model) and (effort is None or c[1] == effort)
    ]
    if not cands:
        raise BriefError(
            f"no {work} tier of profile {rules.profile} takes "
            f"{model or 'any model'}@{effort or 'any effort'}:\n{rules.table_text()}"
        )
    for fam, eff, rule in cands:
        if rules.accepts(rule, set(known)):
            return fam, eff, known, notes
    best = [
        (len(m), i, m)
        for i, (_, _, r) in enumerate(cands)
        if (m := _missing(r, set(known))) is not None
    ]
    if best:
        _, i, add = min(best)
        extra = sorted(add)
    else:
        extra = [DEFAULT_TAG[work]]
        i = next((j for j, c in enumerate(cands) if rules.accepts(c[2], {*known, *extra})), None)
        if i is None:
            raise BriefError(
                f"no {work} tier of profile {rules.profile} accepts tags "
                f"{', '.join(known) or '(none)'}:\n{rules.table_text()}"
            )
    notes.append(
        f"added {noun(len(extra), 'tag')} {', '.join(extra)}: keep "
        f"{'it' if len(extra) == 1 else 'them'} only if true, else name the tags that are"
    )
    fam, eff, _ = cands[i]
    return fam, eff, [*known, *extra], notes


def brief(
    rules: model_choice.Rules,
    work: str,
    tags: list[str],
    *,
    owner_asked: str | None = None,
    repo: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    pool: str | None = None,
    agent_type: str | None = None,
    type_why: str | None = None,
    ui: bool = False,
) -> Brief:
    """The header and Agent call for one spawn under the rules' active profile."""
    if work not in DEFAULT_TAG:
        raise BriefError(f"--work is write or read, not {work!r}")
    fam, eff, decl, notes = resolve(rules, work, tags, model, effort)
    lines = [f"Model-choice: {fam}@{eff} because {', '.join(decl)}"]
    task_class = rules.pool_class(fam, eff)
    if task_class is not None:
        lines.append(_pool_line(rules, work, task_class, pool, repo))
    lean = rules.lean
    sub = lean.get("ui") if ui else lean.get("to")
    rule = rules.rule(fam, eff) or {}
    if (
        agent_type
        and agent_type in (lean.get("from") or [])
        and rule.get("tier") not in (lean.get("read_only_tiers") or [])
    ):
        if not type_why:
            raise BriefError(
                f"--agent-type {agent_type} on write work needs --type-why: the "
                f"spawn guard refuses it otherwise; the lean type is {lean.get('to')}"
            )
        lines.append(f"Agent-type: {agent_type} because {type_why}")
        sub = agent_type
    elif agent_type:
        sub = agent_type
    if owner_asked:
        lines.append(f"Owner-asked: {owner_asked}")
    return Brief(fam, eff, str(sub or "general-purpose"), decl, lines, notes)


def _pool_line(
    rules: model_choice.Rules, work: str, task_class: str, pool: str | None, repo: str | None
) -> str:
    if pool:
        return f"Pool: {pool}"
    if work == "write":
        where = f"in {repo}" if repo else "in this repo"
        return (
            f"Pool: unfit because it edits, builds and runs tests {where}, which the pool's "
            f"backends never do"
        )
    tool = rules.pool_first.get("tool")
    return f'Pool: tried <fill in what {tool}(task_class="{task_class}") returned>'


def _tags_of(prompt: str, declared: re.Pattern[str]) -> list[str] | None:
    decls = declared.findall(prompt)
    if not decls:
        return None
    return [t for t in re.split(r"[\s,+]+", decls[0][3]) if t]


def corrected(
    rules: model_choice.Rules,
    prompt: str,
    agent_type: str,
    declared: re.Pattern[str],
    pool_re: re.Pattern[str],
    type_re: re.Pattern[str],
    owner: str | None,
) -> str:
    """The header a refused spawn would have passed with, for its refusal reason (agent_guard).
    Work is read when the prompt declares `read-only`, else write; the prompt's own Pool,
    Agent-type and Owner-asked lines are kept."""
    tags = _tags_of(prompt, declared) or []
    work = "read" if "read-only" in tags else "write"
    pools = [f"{said} {why}".strip() for said, why in pool_re.findall(prompt) if why]
    escape = next((w for t, w in type_re.findall(prompt) if t == agent_type), None)
    try:
        b = brief(
            rules,
            work,
            tags,
            owner_asked=owner,
            pool=pools[0] if pools else None,
            agent_type=agent_type
            if escape or agent_type not in (rules.lean.get("from") or [])
            else None,
            type_why=escape,
        )
    except BriefError as e:
        return f"No header can be generated: {e}"
    args = f"goh brief --work {work} --tags {','.join(b.tags)}"
    return f"Corrected header ({args}; replace the prompt's header lines):\n{b.text()}"


def apply(prompt: str, header: str) -> tuple[str, dict[str, str]]:
    """(the prompt with `header`'s lines in place of its own, the Agent call's fields): the
    correction applied as a caller would, for tests and scripts."""
    lines = [ln for ln in header.splitlines() if ln.split(":")[0] in HEADER_KEYS]
    call = next(ln for ln in header.splitlines() if ln.startswith("Agent call:"))
    fields = dict(kv.split("=", 1) for kv in call.removeprefix("Agent call:").split())
    return "\n".join(lines) + "\n" + HEADER_LINE.sub("", prompt), fields


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goh brief", description=__doc__.splitlines()[0])
    ap.add_argument("--work", required=True, choices=sorted(DEFAULT_TAG))
    ap.add_argument("--tags", default="", help="comma-separated tags from model_choice.json")
    ap.add_argument("--owner-asked", help="the owner's own words, as the `Owner-asked:` line")
    ap.add_argument("--repo", help="the repo the work is in (named in the `Pool: unfit` line)")
    ap.add_argument("--model", help="pin the model family (else the cheapest accepting tier)")
    ap.add_argument("--effort", help="pin the effort")
    ap.add_argument("--pool", help="the Pool line's text: `tried <...>` or `unfit because <...>`")
    ap.add_argument("--agent-type", help="a subagent_type other than the lean one")
    ap.add_argument("--type-why", help="why --agent-type, when it is a heavy type on write work")
    ap.add_argument("--ui", action="store_true", help="on-screen QA: the lean UI type")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        rules = model_choice.load()
        b = brief(
            rules,
            a.work,
            [t for t in re.split(r"[\s,+]+", a.tags) if t],
            owner_asked=a.owner_asked,
            repo=a.repo,
            model=a.model,
            effort=a.effort,
            pool=a.pool,
            agent_type=a.agent_type,
            type_why=a.type_why,
            ui=a.ui,
        )
    except BriefError as e:
        print(f"goh brief: {e}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError) as e:
        print(f"goh brief: the model-choice rules could not be read ({e!r})", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(b.as_dict() | {"profile": rules.profile_line()}, indent=2))
    else:
        print(b.text())
        print(f"Rules in force: {rules.profile_line()}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
