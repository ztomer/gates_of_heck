"""A land's waived reds: main's own, and a rule newer than the item (GOH.MAINREDWAIVE,
GOH.RULEINFLIGHT; lib/land_waive.py decides what may be waived, this applies it).

GOH_LAND_WAIVE (the lander's, lib/land_waive.py `words`) is JSON: main's red nodes per sha, each by
the id of the reading on main that saw it red, and the rules main gained after every item of the
land forked. The controller resolves it ONCE against the base this run's tree really has --
`git merge-base HEAD <main's HEAD>` -- and hands the result to its workers (xdist `workerinput`):

  * a failing test red on main at THAT base by a reading is waived: reported xfailed, naming the
    reading; a test green at the base has no reading, so it fails as before;
  * a failing rule test in flight is a warning: xfailed, naming the rule, the commit that brought
    it and that the item still owes the fix.

Each waived test is said on one `goh-waived: {json}` line at the run's end, which the land record
keeps (`waived`, lib/land_waive.py `fields`). A waived test that passes is simply green. The key
leaves the environment as the plugin loads, so no session a test starts waives anything.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from timing_bounds import patience

KEY = "GOH_LAND_WAIVE"
WORKER_KEY = "goh_land_waive"
LINE = "goh-waived: "  # lib/land_waive.py LINE
_RAW = os.environ.pop(KEY, "")
_plan: dict[str, dict] = {}
_ready: list[bool] = []
_waived: dict[str, dict] = {}


def _git(cwd: str, *args: str) -> str:
    try:
        r = subprocess.run(["git", "--no-optional-locks", "-C", cwd, *args], capture_output=True,
                           text=True, timeout=patience(60), stdin=subprocess.DEVNULL,
                           check=False)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def resolve(raw: str, root: str) -> dict[str, dict]:
    """{node or `file::func`: what waives it} for the tree at ROOT (module doc)."""
    try:
        got = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    if not isinstance(got, dict) or not got.get("repo"):
        return {}
    main_head = _git(str(got["repo"]), "rev-parse", "HEAD")
    base = _git(root, "merge-base", "HEAD", main_head) if main_head else ""
    plan: dict[str, dict] = {}
    for node, rid in ((got.get("main") or {}).get(base) or {}).items():
        plan[node] = {"node": node, "kind": "main-red", "reading": rid, "sha": base,
                      "reason": f"red on main {base[:8]} by reading {rid}: main's, not this "
                                "item's (GOH.MAINREDWAIVE)"}  # fmt: skip
    for node, info in (got.get("rules") or {}).items():
        by = info.get("commit") or "a commit after the item's base"
        why = (
            f"WARNING: rule {node} ({by}) landed after this item's base: it warns this land, "
            "the item still owes the fix and is red on it next land (GOH.RULEINFLIGHT)"
        )
        plan.setdefault(node, {"node": node, "kind": "rule-in-flight", "rule": node, "commit": by,
                               "items": got.get("items") or [], "reason": why})  # fmt: skip
    return plan


def _controller(config) -> bool:
    return not hasattr(config, "workerinput")


def _load(config) -> dict[str, dict]:
    if not _ready:
        if _controller(config):
            _plan.update(resolve(_RAW, str(config.rootpath)))
        else:
            _plan.update(json.loads(config.workerinput.get(WORKER_KEY) or "{}"))
        _ready.append(True)
    return _plan


def match(plan: dict[str, dict], nodeid: str) -> dict | None:
    """What waives NODEID: its own id, its unparametrized id, or its file and function."""
    bare = nodeid.split("[")[0]
    parts = bare.split("::")
    return plan.get(nodeid) or plan.get(bare) or plan.get(f"{parts[0]}::{parts[-1]}")


def _waives(item) -> dict | None:
    return match(_load(item.config), item.nodeid)


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node) -> None:
    """xdist, in the controller: each worker gets the resolved plan."""
    node.workerinput[WORKER_KEY] = json.dumps(_load(node.config))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    rep = yield
    on = _RAW or not _controller(item.config)
    if rep.failed and rep.when in ("setup", "call") and on and (w := _waives(item)) is not None:
        rep.outcome = "skipped"
        rep.wasxfail = w["reason"]
    return rep


def pytest_runtest_logreport(report) -> None:
    reason = getattr(report, "wasxfail", "")
    ours = "GOH.MAINREDWAIVE" in reason or "GOH.RULEINFLIGHT" in reason
    if ours and _ready and (w := match(_plan, report.nodeid)) is not None:
        _waived[report.nodeid] = {**w, "node": report.nodeid}


@pytest.hookimpl(trylast=True)
def pytest_terminal_summary(terminalreporter) -> None:
    for w in _waived.values():
        terminalreporter.write_line(LINE + json.dumps(w, sort_keys=True))
