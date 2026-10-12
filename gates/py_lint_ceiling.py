#!/usr/bin/env python3
"""py_lint_ceiling.py — ruff findings a repo carries, as a ceiling per file that may only fall.

`py_staged.sh` runs it in place of a plain `ruff check` when `GOH_PY_LINT_CEILINGS` names a JSON file
of `{"path.py": count}` (BACKLOG 4.4). A staged file may carry up to its ceiling; over it fails with
its findings; under it fails with the number to write, so the ratchet never holds room for a finding
to come back; a file not listed carries none. The ceilings are read from the INDEX (what is being
committed): one that is missing from it, or higher than HEAD's, is a finding -- deleting the file or
raising a number is how debt is reset, and a ceiling that can be reset proves nothing.

    py_lint_ceiling.py --check --ceilings tools/py_lint_ceilings.json --runner "python3 -m" -- a.py b.py
    py_lint_ceiling.py --seed tools/py_lint_ceilings.json [--runner "python3 -m"]   # every tracked .py

Exit 0 pass, 1 a finding, 2 a precondition (the key names no committed file, ruff unreadable).
"""

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
import collections
import json
import shlex
import subprocess
import sys
from pathlib import Path

KEY = "GOH_PY_LINT_CEILINGS"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False)


def _ceilings_at(rev: str, path: str) -> dict[str, int] | None:
    """The ceilings at `rev` (":" is the index), or None where the file is not there."""
    shown = _git("show", f"{rev}{path}" if rev == ":" else f"{rev}:{path}")
    if shown.returncode != 0:
        return None
    try:
        data = json.loads(shown.stdout)
    except json.JSONDecodeError as e:
        raise SystemExit(f"✗ {KEY}: {path} at {rev} is not JSON ({e})") from None
    if not isinstance(data, dict) or not all(isinstance(v, int) for v in data.values()):
        raise SystemExit(f'✗ {KEY}: {path} must be {{"path.py": count}}')
    return data


def findings(runner: str, files: list[str]) -> dict[str, list[dict]]:
    """Each file's ruff findings, keyed by its path relative to the repo root."""
    if not files:
        return {}
    cmd = [*shlex.split(runner), "ruff", "check", "--output-format", "json", "--exit-zero", *files]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    try:
        found = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        print(
            f"✗ ruff gave no report (exit {r.returncode}): {r.stderr.strip()[:300]}",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    root = Path.cwd().resolve()
    by_file: dict[str, list[dict]] = collections.defaultdict(list)
    for f in found:
        by_file[str(Path(f["filename"]).resolve().relative_to(root))].append(f)
    return by_file


def check(path: str, runner: str, files: list[str]) -> int:
    staged = _ceilings_at(":", path)
    if staged is None:
        print(
            f"✗ {KEY}={path} names a file that is not in this commit: deleting the ceilings resets"
            " the debt they count. Restore it, or unset the key in .gatesrc",
            file=sys.stderr,
        )
        return 2
    problems = []
    head = _ceilings_at("HEAD", path)
    if head is not None:
        raised = {k: v for k, v in staged.items() if v > head.get(k, 0)}
        problems += [
            f"{k}: ceiling raised {head.get(k, 0)} -> {v} in {path}"
            for k, v in sorted(raised.items())
        ]
    found = findings(runner, files)
    for f in sorted(files):
        have, ceiling = len(found.get(f, [])), staged.get(f, 0)
        if have > ceiling:
            problems.append(f"{f}: {have} ruff finding(s), ceiling {ceiling}")
            problems += [
                f"    {f}:{x['location']['row']}:{x['location']['column']} {x['code']} {x['message']}"
                for x in found[f]
            ]
        elif have < ceiling:
            problems.append(
                f"{f}: {have} ruff finding(s), under its ceiling {ceiling} -- write {have} in {path}"
            )
    for p in problems:
        print(f"✗ {p}" if not p.startswith(" ") else p, file=sys.stderr)
    if not problems:
        print(f"✓ ruff check: {len(files)} staged file(s) within {path}")
    return 1 if problems else 0


def seed(out: str, runner: str) -> int:
    tracked = [f for f in _git("ls-files", "*.py").stdout.splitlines() if f]
    counts = {f: len(v) for f, v in sorted(findings(runner, tracked).items()) if v}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(counts, indent=2, sort_keys=True) + "\n")
    print(
        f"✓ {out}: {sum(counts.values())} finding(s) in {len(counts)} of {len(tracked)} tracked file(s)"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check", action="store_true", help="judge the staged files against the ceilings"
    )
    mode.add_argument("--seed", metavar="OUT", help="write today's count for every tracked .py")
    ap.add_argument("--ceilings", help="the ceilings file (with --check)")
    ap.add_argument(
        "--runner", default="python3 -m", help="how ruff is run (GOH_PY_RUNNER's shape)"
    )
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()
    if a.seed:
        return seed(a.seed, a.runner)
    if not a.ceilings:
        ap.error("--check needs --ceilings")
    return check(a.ceilings, a.runner, a.files)


if __name__ == "__main__":
    sys.exit(main())
