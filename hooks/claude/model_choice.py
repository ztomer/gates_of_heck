"""The ONE reader of `model_choice.json`: the subagent model-choice rules and the facts they rest on.

`agent_guard.py` (the spawn hook), `scripts/agent_audit.py` (the ledger's verdicts) and
`scripts/model_choice_watch.py` (the weekly re-check against the sources) all read the rules
through this module, so no table, price, alias or version is spelled anywhere else
(tests/test_model_choice_rules.py). The file is the owner's: nothing here writes it.

`GOH_MODEL_CHOICE` names another rules file (tests; an owner trying a change before committing it).

PROFILES (owner 2026-11-15, GOH.ULTRARULES). The file's `profiles` names pro, max and ultra; each
one's `changes` are laid over the top-level `tiers`, `pool_first` and `exploration`. The ACTIVE
profile is the one the owner declared (`profiles.declared`), dropped one step along
`profiles.order` by the budget guard (`profiles.budget_guard`) when token-pool's meter says the
plan's headroom is not there or cannot be seen (`active_profile`). `Rules(..., profile=name)`
gives one profile's rules without asking the meter (the review weighs each).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().with_name("model_choice.json")
# The tag that says a red->green test and CI will catch a miss. Exploration never routes a spawn
# without it to the cheap tier, whatever the rules file says: that is the bound on exploring.
MECHANICAL_CHECK = "checkable"
PRICE_KEYS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
# Ledger token key -> price key. Cache writes are priced at the 5-minute rate, Claude Code's.
TOKEN_PRICE = {"input": "input", "output": "output", "cache_creation": "cache_write_5m",
               "cache_read": "cache_read"}  # fmt: skip


def rules_path() -> Path:
    return Path(os.environ.get("GOH_MODEL_CHOICE") or DEFAULT_PATH)


PROFILED = ("tiers", "pool_first", "exploration")  # the top-level keys a profile's changes overlay


def overlay(base: Any, change: Any) -> Any:
    """`change` laid over `base`: dicts merge key by key, anything else replaces."""
    if isinstance(base, dict) and isinstance(change, dict):
        merged = {k: overlay(v, change[k]) if k in change else v for k, v in base.items()}
        return merged | {k: v for k, v in change.items() if k not in base}
    return change


RULES_KEY = "model_choice.json `profiles.declared`"


def _left(text: str, window: str, pattern: str) -> int | None:
    m = re.search(pattern.replace("{window}", re.escape(window)), text)
    return int(m.group(1)) if m else None


def read_meter(guard: dict[str, Any], now: dt.datetime | None = None) -> dict[str, Any]:
    """The budget guard's reading of token-pool's meter: {"ok": bool, "why": str, "7d": pct, ...}.

    Never raises: a meter that cannot be read is a reading that says so (`ok` False)."""
    now = now or dt.datetime.now(dt.UTC)
    base = os.environ.get(guard["meter_dir_env"]) or os.path.expanduser(guard["meter_dir"])
    path = Path(base) / guard["meter_file"]
    window, floor, max_age = guard["window"], guard["min_left_pct"], guard["max_age_h"]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        src = next(s for s in doc["sources"] if s.get("id") == guard["source"])
        when = dt.datetime.fromisoformat(str(src.get("read_at") or doc["read_at"]))
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as e:
        return {"ok": False, "why": f"the {guard['source']} meter could not be read ({path}: "
                                    f"{type(e).__name__})"}  # fmt: skip
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    text = f"{src.get('why') or ''} {src.get('short') or ''}"
    out: dict[str, Any] = {"read_at": when.isoformat(timespec="seconds")}
    for w in guard["windows"]:
        out[w] = _left(text, w, guard["left_pattern"])
    age_h = (now - when).total_seconds() / 3600
    out["age_h"] = round(age_h, 2)
    left = out[window]
    if src.get("state") != "value" or left is None:
        return out | {"ok": False, "why": f"the {guard['source']} meter could not be read (state "
                                          f"{src.get('state')!r}, no {window} figure)"}  # fmt: skip
    if age_h > max_age:
        return out | {"ok": False, "why": f"the {guard['source']} {window} meter reading is "
                                          f"{age_h:.1f} h old (over {max_age} h)"}  # fmt: skip
    if left < floor:
        return out | {"ok": False, "why": f"the {guard['source']} {window} meter reads {left}% "
                                          f"left (below {floor}%)"}  # fmt: skip
    return out | {"ok": True, "why": f"the {guard['source']} {window} meter reads {left}% left "
                                     f"(at least {floor}%)"}  # fmt: skip


def active_profile(data: dict[str, Any], now: dt.datetime | None = None) -> tuple[str, str, Any]:
    """(the active profile, why it is active, the meter reading) for a rules file's data."""
    prof = data.get("profiles")
    if not prof:
        return "", "the rules file names no profiles", None
    order, declared = prof["order"], prof["declared"]
    reading = read_meter(prof["budget_guard"], now)
    if reading["ok"] or order.index(declared) == len(order) - 1:
        floor = "" if reading["ok"] else ", the lowest profile"
        return declared, f"declared {declared} in {RULES_KEY}{floor}; {reading['why']}", reading
    name = order[order.index(declared) + 1]
    return name, (f"declared {declared} in {RULES_KEY}, dropped one profile by the budget guard: "
                  f"{reading['why']}"), reading  # fmt: skip


class Rules:
    """The rules file, read once, with the lookups its readers need.

    `profile` None resolves the active profile (the declared one, narrowed by the meter); a name
    gives that profile's rules as they are, the meter unread."""

    def __init__(self, data: dict[str, Any], path: Path, profile: str | None = None) -> None:
        self.data, self.path = data, path
        self.meter: Any = None
        if profile is None:
            profile, self.profile_why, self.meter = active_profile(data)
        else:
            self.profile_why = f"{profile}, as named (the meter unread)"
        self.profile = profile
        changes = (data.get("profiles") or {}).get(profile, {}).get("changes") or {}
        eff = {k: overlay(data.get(k) or {}, changes[k]) if k in changes else data.get(k)
               for k in PROFILED}  # fmt: skip
        self.facts: dict[str, dict[str, Any]] = data["facts"]
        self.tiers: dict[str, dict[str, Any]] = eff["tiers"]
        self.families: tuple[str, ...] = tuple(self.facts["agent.models"]["value"])
        self.efforts: tuple[str, ...] = tuple(self.facts["agent.efforts"]["value"])
        self.exempt = frozenset(data["exempt_agent_types"]["value"])
        self.exploration: dict[str, Any] = eff["exploration"] or {}
        self.pool_first: dict[str, Any] = eff["pool_first"] or {}
        self.lean: dict[str, Any] = data.get("lean_types") or {}
        self.verdict: dict[str, Any] = data["verdict"]
        self.tags = frozenset(
            t for v in self.tiers.values() if v["tier"] != "never"
            for t in [*v["tags"], *v.get("also_all", [])]
        )  # fmt: skip

    def profile_line(self) -> str:
        """The active profile and why, as every refusal names it."""
        return f"profile {self.profile} ({self.profile_why})" if self.profile else ""

    def accepts(self, rule: dict[str, Any], tags: set[str]) -> bool:
        """Whether a spawn's tags meet a tier's `need`, or its `also_all` set in full."""
        need = set(rule["tags"])
        met = need <= tags if rule["need"] == "all" else bool(need & tags)
        also = set(rule.get("also_all") or [])
        return met or (bool(also) and also <= tags)

    def rule(self, fam: str, effort: str) -> dict[str, Any] | None:
        for key in (f"{fam}@{effort}", f"*@{effort}", f"{fam}@*"):
            if key in self.tiers:
                return self.tiers[key]
        return None

    def pool_class(self, fam: str, effort: str) -> str | None:
        """The token-pool task class this spawn's work goes to first, else None (not pool work)."""
        rule = self.rule(fam, effort)
        if rule is None or rule["tier"] not in (self.pool_first.get("tiers") or []):
            return None
        return self.pool_first["task_class"][rule["tier"]]

    def classes(self, *tiers: str) -> list[str]:
        """The concrete `model@effort` keys whose tier is one of `tiers` (wildcards left out)."""
        return [k for k, v in self.tiers.items() if v["tier"] in tiers and "*" not in k]

    def table_text(self) -> str:
        """The tier table in a few lines: rows with the same rule share one line."""
        rows: dict[tuple[str, str], list[str]] = {}
        for key, v in self.tiers.items():
            if v["tier"] == "never":
                rows.setdefault(("never", ""), []).append(key)
                continue
            sep = " + " if v["need"] == "all" else ", "
            text = sep.join(sorted(v["tags"]))
            if v.get("also_all"):
                text += f"; or {' + '.join(v['also_all'])}"
            rows.setdefault((v["need"], text), []).append(key)
        lines = []
        for (need, text), keys in rows.items():
            if need == "never":
                whys = dict.fromkeys(self.tiers[k]["why"] for k in keys)  # unique, in order
                lines.append(f"  never: {', '.join(keys)} -- {'; '.join(whys)}")
            else:
                lines.append(f"  {', '.join(keys)}: {'one of ' if need == 'any' else ''}{text}")
        return "\n".join(lines)

    def price(self, fam: str | None) -> dict[str, float] | None:
        fact = self.facts.get(f"price.{fam}")
        return fact["value"] if fact else None

    def cost(self, fam: str | None, tokens: Any) -> float | None:
        """USD for one agent's tokens (input/output/cache_creation/cache_read) at `fam`'s prices;
        None when the model has no price or the tokens are not broken down by kind."""
        price = self.price(fam)
        if price is None or not isinstance(tokens, dict):
            return None
        if not all(isinstance(tokens.get(k), int) for k in TOKEN_PRICE):
            return None
        return sum(tokens[k] * price[p] for k, p in TOKEN_PRICE.items()) / 1e6

    def route(self, cls: str, tags: list[str], sha: str) -> tuple[str, str] | None:
        """(the class to run instead, why) for a spawn exploration picks, else None.

        Deterministic: the choice is a function of the prompt's sha, so the same prompt always
        lands on the same tier and a respawn cannot shop for one."""
        got = self._route(cls, tags, sha)
        if got is None:
            return None
        target = self.rule(*got[0].split("@"))
        # A profile can make an exploration target `never` (ultra, pro): never routed there.
        return None if target is None or target["tier"] == "never" else got

    def _route(self, cls: str, tags: list[str], sha: str) -> tuple[str, str] | None:
        tagset, n = set(tags), int(sha, 16)
        if MECHANICAL_CHECK not in tagset:
            return None
        alt = self.exploration.get("cheap_alternation") or {}
        between = list(alt.get("between") or [])
        if alt.get("enabled") and cls in between:
            return between[n % len(between)], "cheap-alternation"
        down = self.exploration.get("opus_to_cheap") or {}
        if not (down.get("enabled") and between and cls in (down.get("from") or [])):
            return None
        if not set(down.get("requires_tags") or []) <= tagset:
            return None
        if set(down.get("refuses_tags") or []) & tagset:
            return None
        if (n >> 8) % 10_000 >= round(float(down.get("fraction") or 0) * 10_000):
            return None
        return between[(n >> 1) % len(between)], "opus-to-cheap"


def load(path: Path | None = None, profile: str | None = None) -> Rules:
    """The rules at `path` (default: GOH_MODEL_CHOICE, else the file beside this module), under
    `profile` (default: the active one). Raises OSError/ValueError/KeyError on a file that is
    missing or not the expected shape."""
    path = path or rules_path()
    return Rules(json.loads(path.read_text(encoding="utf-8")), path, profile)


def family(model: str, families: tuple[str, ...]) -> str | None:
    """The alias a model names -- `sonnet` for `sonnet` or `claude-sonnet-5-5[1m]` -- else None."""
    words = re.split(r"[^a-z0-9]+", model.lower())
    return next((w for w in words if w in families), None)
