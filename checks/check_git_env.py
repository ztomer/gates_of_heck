#!/usr/bin/env python3
"""Gate: every git spawn in a repo goes through its seam, or says why it does not (BACKLOG 6.1).

    check_git_env.py                     # the census, configured by <gate dir>/git_env.json
    check_git_env.py --config PATH       # another config
    check_git_env.py --probe             # prove this gate can go red

WHY HERE. git hands every hook GIT_DIR, and git ignores `-C` and `cwd` once it is set, so a git
call aimed at a fixture from a hook answers about (or re-initialises) the hook's repository. The
census of where a git argv may be built is `_gitcensus.census` (4.1); the environment a call aimed
elsewhere takes is `_gitutil.foreign_repo_env()`, which drops what `git rev-parse
--local-env-vars` lists (git's own list, `GIT_PREFIX` among them, never typed). What three
consumers still wrote by hand was the GATE around them, and the hand-written copies walked the
disk: in `check_empty_scope`'s skeleton the gate directory is on disk but ignored, so a walk found
the gates themselves and passed over a tree with no content ("PASSED over an empty tree"). This
gate reads git's listing through the census, and a census that read no Python REFUSES, naming the
sources it looked in, rather than reporting compliance over nothing.

THE CONFIG, `git_env.json` beside the repo's gates (`tools/` or `checks/`):

    {"sources": ["tools", "tests"],
     "exempt":  {"tools/git_env.py": "the seam itself: where a git call is built"},
     "inherit": {"tools/gate_tests/": "runs this repo's own gate; the hook's index is the point"},
     "shell":   {"tools/gate.sh": "rev-parse --show-toplevel of the tree the hook fired in"}}

`exempt` declares a file that spawns git raw, `inherit` a file or directory whose spawns in
another directory may keep the caller's environment, `shell` a shell script that runs git (the
census reads Python only; each is said here, and one that no longer mentions git is stale). Every
declaration carries a reason, and one that no longer matches anything is a finding: a permission
nobody uses is the next site's permission.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import argparse
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _gitcensus import census
from _gitutil import foreign_repo_env, repo_root

from tui.lib import err, ok

CONFIG = "git_env.json"
GATE_DIRS = ("tools", "checks")
SECTIONS = ("exempt", "inherit", "shell")
# A shell line runs git when the word `git` starts a command: after a line start, `;`, `|`, `&`,
# `(`, `$(` or a backtick.
SHELL_GIT = re.compile(r"(^|[;|&(`]|\$\()\s*git(\s|$)", re.MULTILINE)
REMEDY = (
    "route it through the repo's git seam (a call that names another repository takes "
    "_gitutil.foreign_repo_env()), or declare the file in {config} with the reason"
)


def find_config(root: str) -> str | None:
    for name in GATE_DIRS:
        path = os.path.join(root, name, CONFIG)
        if os.path.isfile(path):
            return path
    return None


def load(path: str) -> tuple[dict, list]:
    """The config and what is wrong with its shape: no sources, or a declaration with no reason."""
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    problems = []
    sources = cfg.get("sources")
    if not (isinstance(sources, list) and sources and all(isinstance(s, str) for s in sources)):
        problems.append(f"{path}: `sources` must name at least one directory")
    for section in SECTIONS:
        for key, why in (cfg.get(section) or {}).items():
            if not (isinstance(why, str) and why.strip()):
                problems.append(f"{path}: {section} `{key}` gives no reason")
    return cfg, problems


def judge(root: str, cfg: dict, config_name: str) -> tuple[list, str]:
    """(findings, the summary line when there are none)."""
    sources = cfg["sources"]
    got = census(root, sources, exempt=cfg.get("exempt"), inherit=cfg.get("inherit"))
    remedy = REMEDY.format(config=config_name)
    found = []
    if got.read == 0:
        found.append(
            f"the census read no Python under {', '.join(sources)}: 'every git spawn is "
            f"accounted for' would be a claim about nothing"
        )
    found += [f"{rel}: not readable as Python ({why})" for rel, why in got.unreadable]
    found += [
        f"{s.path}:{s.line} spawns git directly -- {remedy}: {s.source[:110]}" for s in got.sites
    ]
    found += [
        f"{s.path}:{s.line} runs in another directory with the caller's environment, so a hook's "
        f"GIT_DIR goes with it -- pass env=_gitutil.foreign_repo_env(), or declare it under "
        f"`inherit` with the reason: {s.source[:110]}"
        for s in got.fixture_sites
    ]
    found += [
        f"exempt `{rel}` no longer spawns git raw -- delete the declaration" for rel in got.stale
    ]
    found += [
        f"inherit `{key}` covers no spawn in another directory -- delete the declaration"
        for key in got.stale_inherit
    ]
    for rel in cfg.get("shell") or {}:
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                runs_git = bool(SHELL_GIT.search(fh.read()))
        except OSError:
            runs_git = False
        if not runs_git:
            found.append(f"shell `{rel}` runs no git -- delete the declaration")
    summary = (
        f"git env: every git spawn in {', '.join(sources)} goes through the seam or says why "
        f"({got.read} Python file(s) read; {len(cfg.get('exempt') or {})} exempt, "
        f"{len(cfg.get('inherit') or {})} inheriting, {len(cfg.get('shell') or {})} shell site(s))"
    )
    return found, summary


def run(root: str, config: str | None) -> int:
    path = config or find_config(root)
    if not path:
        err(
            f"no {CONFIG} in {' or '.join(GATE_DIRS)}/: this gate has nothing to census -- "
            f"write one (see `check_git_env.py`'s docstring) or drop the gate"
        )
        return 1
    cfg, problems = load(path)
    if problems:
        for line in problems:
            err(line)
        return 1
    found, summary = judge(root, cfg, os.path.relpath(path, root))
    for line in found:
        err(line)
    if found:
        return 1
    ok(summary)
    return 0


def _write(root: str, rel: str, text: str) -> None:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def probe() -> int:
    """Each refusal, over a temp tree: a raw spawn, an empty read, a stale declaration, and a call
    through the seam left alone. A gate nobody has watched go red reports nothing. Its trees are
    fixtures, so git here must not inherit a hook's repository."""
    scrubbed = foreign_repo_env()
    os.environ.clear()
    os.environ.update(scrubbed)
    cfg = {"sources": ["tools"], "exempt": {}, "inherit": {}, "shell": {}}
    cases = {
        "a raw git spawn": ('import subprocess\nsubprocess.run(["git", "status"])\n', {}, 1),
        "a call through the seam": ('import git_env\ngit_env.run(["git", "status"])\n', {}, 0),
        "a stale exemption": ("x = 1\n", {"exempt": {"tools/a.py": "it used to"}}, 1),
        "a shell site with no git": ("x = 1\n", {"shell": {"tools/a.py": "it used to"}}, 1),
    }
    bad = 0
    for name, (text, extra, want) in cases.items():
        with tempfile.TemporaryDirectory(prefix="goh-git-env-probe.") as td:
            _write(td, "tools/a.py", text)
            got = len(judge(td, {**cfg, **extra}, CONFIG)[0])
        if got != want:
            err(f"probe: {name}: {got} finding(s), wanted {want}")
            bad += 1
    with tempfile.TemporaryDirectory(prefix="goh-git-env-probe.") as td:
        os.makedirs(os.path.join(td, "tools"))
        if not any("read no Python" in f for f in judge(td, cfg, CONFIG)[0]):
            err("probe: a census that read nothing passed")
            bad += 1
    if bad:
        return 1
    ok("check_git_env --probe: a raw spawn, an empty read and a stale declaration each refuse")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", help=f"the census config (default: <gate dir>/{CONFIG})")
    parser.add_argument("--probe", action="store_true", help="prove this gate can go red")
    args = parser.parse_args()
    if args.probe:
        return probe()
    return run(repo_root() or os.getcwd(), args.config)


if __name__ == "__main__":
    sys.exit(main())
