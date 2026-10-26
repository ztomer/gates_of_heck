"""The ONE reader of `model_choice.json`: the subagent model-choice rules and the facts they rest on.

`agent_guard.py` (the spawn hook), `scripts/agent_audit.py` (the ledger's verdicts) and
`scripts/model_choice_watch.py` (the weekly re-check against the sources) all read the rules
through this module, so no table, price, alias or version is spelled anywhere else
(tests/test_model_choice_rules.py). The file is the owner's: nothing here writes it.

`GOH_MODEL_CHOICE` names another rules file (tests; an owner trying a change before committing it).
"""

from __future__ import annotations

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


class Rules:
    """The rules file, read once, with the lookups its readers need."""

    def __init__(self, data: dict[str, Any], path: Path) -> None:
        self.data, self.path = data, path
        self.facts: dict[str, dict[str, Any]] = data["facts"]
        self.tiers: dict[str, dict[str, Any]] = data["tiers"]
        self.families: tuple[str, ...] = tuple(self.facts["agent.models"]["value"])
        self.efforts: tuple[str, ...] = tuple(self.facts["agent.efforts"]["value"])
        self.exempt = frozenset(data["exempt_agent_types"]["value"])
        self.exploration: dict[str, Any] = data.get("exploration", {})
        self.pool_first: dict[str, Any] = data.get("pool_first") or {}
        self.verdict: dict[str, Any] = data["verdict"]
        self.tags = frozenset(
            t for v in self.tiers.values() if v["tier"] != "never" for t in v["tags"]
        )

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
            rows.setdefault((v["need"], sep.join(sorted(v["tags"]))), []).append(key)
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


def load(path: Path | None = None) -> Rules:
    """The rules at `path` (default: GOH_MODEL_CHOICE, else the file beside this module).
    Raises OSError/ValueError/KeyError on a file that is missing or not the expected shape."""
    path = path or rules_path()
    return Rules(json.loads(path.read_text(encoding="utf-8")), path)


def family(model: str, families: tuple[str, ...]) -> str | None:
    """The alias a model names -- `sonnet` for `sonnet` or `claude-sonnet-5-5[1m]` -- else None."""
    words = re.split(r"[^a-z0-9]+", model.lower())
    return next((w for w in words if w in families), None)
