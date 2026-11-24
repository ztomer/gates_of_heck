"""lib/land_waive.py -- which reds are an item's own: main's reds and newer rules are not
(GOH.MAINREDWAIVE, GOH.RULEINFLIGHT).

Two kinds of red bill an item for a fault it did not make (2026-11-24: 85 red lands for 45 landed
in 24 h, 2.94 attempts per item):

  MAIN RED (GOH.MAINREDWAIVE). A test already red on main fails every item that runs it. A failing
  test is waived for an item ONLY when a reading ON MAIN at the item's base saw that same test red
  (lib/main_reading.py: the idle full suite, or a probe of named tests on a clean checkout) -- never
  by name, never by a flake guess. A test green at the base that the item turns red has no such
  reading, and still reds the item. The waiver names the reading's id.

  RULE IN FLIGHT (GOH.RULEINFLIGHT). A rule test (a ratchet or tree-wide judge: `is_rule`) that
  main gained after the item forked -- in main's tree, absent from EVERY tree the land's items were
  enqueued at -- judges the item as a WARNING naming the rule: it lands, and still owes the fix. The
  grace is one land: once a land of the item has landed under that warning, the rule reds it. A
  rule the item itself adds, or one at its base, is in its enqueued tree, so it reds as before.

    excuses(reqs)    what the land of REQS (one request, or a batch's stack) may waive: {repo,
                     head (main's tip), main: {sha: {node: reading id}} at main's tip and at each
                     item's fork from it, rules: {node: {commit, why}}, items}; computed outside
                     the suite, where goh.db and git are the lander's
    env(reqs)        `GOH_LAND_WAIVE=<that JSON>` for tools/land.sh's runs ([] when nothing
                     is waivable); the suite's plugin (tests/_land_waive.py) applies it to the base
                     the run's tree really has (`git merge-base HEAD <main>`), so a reading at
                     another sha never waives anything
    fields(outcome)  the land record's `waived`: the `goh-waived:` lines its log carries, one per
                     waived test {node, kind, reading | rule, reason} (lib/land_reds.py `fields`)

Never raises into a land: a waiver that cannot be computed waives nothing.
Pinned by tests/test_main_red_waive.py and tests/test_rule_inflight.py.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitrun
import goh_db
import main_reading
import tree_walk

ENV = "GOH_LAND_WAIVE"
LINE = "goh-waived: "  # the plugin's record line in the gate's log (tests/_land_waive.py)
TESTS = re.compile(r"^tests/(?:.+/)?test_[^/]*\.py$")
FUNC = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+(test_\w+)", re.MULTILINE)
TREE_NAMED = re.compile(r"^test_(?:every|no)_")
GUARD = "# goh-land-guard"


def _git(repo: str, *args: str) -> tuple[int, str]:
    try:
        r = gitrun.run_reader(args, repo=Path(repo), timeout=60)
    except (OSError, ValueError):
        return 1, ""
    return r.returncode, r.stdout


def _show(repo: str, sha: str, rel: str) -> str | None:
    rc, out = _git(repo, "show", f"{sha}:{rel}")
    return out if rc == 0 else None


def funcs(text: str | None) -> set[str]:
    return set(FUNC.findall(text or ""))


def is_rule(rel: str, text: str, func: str) -> bool:
    """A ratchet or tree-wide judge: a `test_every_`/`test_no_` test, a declared land guard, a
    test of a file that lists or walks the tree (tools/affected_tests.py's tree readers), or one
    lib/land_reds.py's rule calls a table judge."""
    import land_reds  # lib/: the table-judge rule over a test id

    return bool(TREE_NAMED.match(func) or GUARD in text or tree_walk.TREE_READ.search(text)
                or land_reds.judges_a_table(f"{rel}::{func}"))  # fmt: skip


def rules_in_flight(repo: str, head: str, enqueued: list[str]) -> dict[str, dict]:
    """{`file::func`: {commit, why}} for each rule test in main's tree at HEAD that every one of
    ENQUEUED lacks (module doc)."""
    found: dict[str, dict] | None = None
    for enq in enqueued:
        rc, fork = _git(repo, "merge-base", enq, head)
        if rc or not fork.strip():
            return {}
        fork = fork.strip()
        rc, names = _git(repo, "diff", "--name-only", "--diff-filter=AM", fork, head, "--", "tests")
        mine: dict[str, dict] = {}
        for rel in names.split() if rc == 0 else []:
            if not TESTS.match(rel) or (text := _show(repo, head, rel)) is None:
                continue
            for func in sorted(funcs(text) - funcs(_show(repo, enq, rel))):
                if is_rule(rel, text, func):
                    mine[f"{rel}::{func}"] = {"since": fork}
        found = mine if found is None else {k: v for k, v in found.items() if k in mine}
    for node, info in (found or {}).items():
        rel, func = node.split("::")
        _, sub = _git(repo, "log", "-1", "--format=%h %s", "-S", f"def {func}(",
                      f"{info['since']}..{head}", "--", rel)  # fmt: skip
        info["commit"] = sub.strip()[:120]
    return found or {}


def warned(items: list[str]) -> set[str]:
    """The rules a LANDED land of one of ITEMS already carried as a warning: grace spent."""
    out: set[str] = set()
    try:
        with goh_db.known() as con:
            for item in items:
                for (body,) in con.execute(
                    "SELECT body FROM lands WHERE item=? AND state='landed'", (item,)
                ):
                    for w in json.loads(body).get("waived") or []:
                        if w.get("kind") == "rule-in-flight" and w.get("node"):
                            out.add(str(w["node"]))
    except (OSError, sqlite3.Error, ValueError):
        pass
    return out


def excuses(reqs: list[dict]) -> dict:
    """What the land of REQS may waive (module doc); {} when nothing."""
    repo = str(reqs[0].get("repo") or "")
    enqueued = [str(r.get("sha") or "") for r in reqs]
    items = [str(r["item"]) for r in reqs if r.get("item")]
    if not repo or not all(enqueued):
        return {}
    rc, head = _git(repo, "rev-parse", "HEAD")
    head = head.strip()
    if rc or not head:
        return {}
    main: dict[str, dict[str, int]] = {}
    for enq in enqueued:
        rc, fork = _git(repo, "merge-base", enq, head)
        for sha in {head, fork.strip() if rc == 0 else ""} - {""}:
            if sha not in main and (red := main_reading.red_at(repo, sha)):
                main[sha] = red
    spent = warned(items)
    rules = {n: i for n, i in rules_in_flight(repo, head, enqueued).items() if n not in spent}
    if not main and not rules:
        return {}
    return {"repo": repo, "head": head, "main": main, "rules": rules, "items": items}


def env(reqs: list[dict]) -> list[str]:
    """`GOH_LAND_WAIVE=<excuses>` for land.sh's runs of REQS; [] when nothing is waivable."""
    try:
        got = excuses([r for r in reqs if r])
    except Exception:  # noqa: BLE001 -- a waiver that cannot be computed waives nothing
        return []
    return [f"{ENV}={json.dumps(got, sort_keys=True)}"] if got else []


def fields(outcome: dict) -> dict:
    """`{"waived": [...]}` from the `goh-waived:` lines of OUTCOME's log; {} with none."""
    try:
        text = Path(str(outcome.get("log") or "")).read_text(errors="replace")
    except OSError:
        return {}
    got: dict[str, dict] = {}
    for line in text.splitlines():
        at = line.find(LINE)
        if at < 0:
            continue
        try:
            w = json.loads(line[at + len(LINE) :])
        except ValueError:
            continue
        if isinstance(w, dict) and w.get("node"):
            got[str(w["node"])] = w
    return {"waived": list(got.values())} if got else {}
