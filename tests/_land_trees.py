"""The trees a land's suite carries verdicts from, their diffs, and what may carry
(tests/_land_verdicts.py).

Split from the plugin for its line cap (GOH.COVCARRY); the plugin imports these names, so a fixture
session patches them there (`lv.trees`, `lv.diff`).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ANCESTORS = 20  # first-parent commits of HEAD searched for a recorded tree


def _git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    return r.stdout.strip() if r.returncode == 0 else ""


def trees(root: Path, pre_sha: str) -> list[tuple[str, str]]:
    """(commit, tree) to carry from, nearest first: the pre-step's commit, then HEAD's first-parent
    ancestors (HEAD's own tree is the pre-step's when nothing rebased)."""
    shas = [pre_sha] if pre_sha else []
    shas += _git(root, "rev-list", "--first-parent", f"--max-count={ANCESTORS}", "HEAD~1").split()
    out = []
    for sha in shas:
        tree = _git(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{tree}}")
        if tree:
            out.append((sha, tree))
    return out


def named(root: Path, shas: list[str]) -> list[tuple[str, str]]:
    """(commit, tree) of each of SHAS git can read: the tips a batch's other items' pre-steps
    judged, which the gate's suite carries from too (GOH.PREONTIP, `<record>.sources`)."""
    out = []
    for sha in shas:
        tree = _git(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{tree}}")
        if tree:
            out.append((sha, tree))
    return out


def sources(record: str, keys: dict[str, str] | None = None) -> list[str]:
    """The shas named beside a gate's RECORD (`<record>.sources`, tools/land.sh); the pre-step's
    own run (RECORD /dev/null), the tree beside the record it writes (KEYS' RECORD): the measured
    run a Rust retry carries from (`.kin`, lib/land_rustcov.py); [] for none."""
    own = (keys or {}).get("GOH_LAND_VERDICTS_RECORD", "")
    path = f"{record}.sources" if record != os.devnull else f"{own}.kin" if own else ""
    try:
        text = Path(path).read_text(encoding="utf-8") if record and path else ""
    except OSError:
        return []
    return [w for w in text.split() if len(w) == 40 and all(c in "0123456789abcdef" for c in w)]


def diff(root: Path, sha: str) -> list[str] | None:
    """The paths whose bytes differ between sha's tree and HEAD's; None when git cannot say."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    r = subprocess.run(["git", "-C", str(root), "diff", "--name-only", "--no-renames", sha, "HEAD"],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
                       check=False)  # fmt: skip
    return [ln for ln in r.stdout.splitlines() if ln] if r.returncode == 0 else None


def carried(passed: dict[str, bool], chosen: set[str], coverage: bool, binary: bool,
            measured: dict[str, bool] | None = None,
            profiled: set[str] | None = None) -> set[str]:  # fmt: skip
    """The nodes of a record of another tree this run may skip: not in a file the diff chooses,
    not a node it chooses (a parametrized node by its function), not one that ran the native
    binary across a change to the binary's build (`binary`), nor under coverage unless the store
    holds its profile of THIS binary (`profiled`, lib/prof_store.py, GOH.COVCARRY3). Under
    coverage a node that ran none is carried only when an instrumented run MEASURED it running
    none (`measured`, tests/_prof_record.py): a mark is no proof (GOH.COVCARRY)."""
    files = {c for c in chosen if "::" not in c}
    out = set()
    for node, ran_bin in passed.items():
        if ran_bin and (binary or (coverage and node not in (profiled or set()))):
            continue
        if coverage and not ran_bin and (measured or {}).get(node) is not False:
            continue
        if node.split("::")[0] in files or node.split("[")[0] in chosen:
            continue
        out.add(node)
    return out


def file_passed(path: str) -> dict[str, bool]:
    """The `passed` of the land record at PATH; {} for none."""
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    passed = rec.get("passed") if isinstance(rec, dict) else None
    return passed if isinstance(passed, dict) else {}


def file_sha(path: str) -> str:
    """The commit the land record at PATH judged; "" for none."""
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return rec["sha"] if isinstance(rec, dict) and isinstance(rec.get("sha"), str) else ""
