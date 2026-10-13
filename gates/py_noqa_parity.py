#!/usr/bin/env python3
"""py_noqa_parity.py — a `# noqa` that ruff honours and a second linter the repo runs ignores.

A repo that runs pyflakes beside ruff has two vocabularies for one decision. pyflakes reads no
`# noqa` at all, so a directive written for ruff silences one gate and leaves the other reporting:
necrohand's `# noqa: F401` quieted ruff while its own pyflakes gate still named four unused imports
in the file the sweep had just cleaned (BACKLOG 4.5). The house forbids Rust's `#[allow]` outright
(`checks/check_no_allow.py`); here the finding is narrower and exact: a line carrying a `# noqa`
that a linter the repo names in `GOH_PY_NOQA_PARITY` still reports. The fix is the code, or one
vocabulary (ruff's F rules alone), never a second directive.

Not claimed: that a directive both linters honour is still RIGHT. ruff's RUF100 proves a `noqa`
unused; whether a used one should stand is a reading, not this check.

    py_noqa_parity.py --linters pyflakes --runner "python3 -m" -- a.py b.py
    py_noqa_parity.py --linters pyflakes --runner "python3 -m" --all     # every tracked .py here

Exit 0 pass, 1 a finding, 2 a precondition (an unknown linter in the key, a linter not runnable).
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
import re
import shlex
import subprocess
import sys
from pathlib import Path

KEY = "GOH_PY_NOQA_PARITY"
# The linters this can read: each honours none of ruff's suppression directives.
LINTERS = ("pyflakes",)
NOQA = re.compile(r"#\s*noqa\b", re.IGNORECASE)
# pyflakes 3: `path:line:col: message`; older: `path:line: message`.
REPORT = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?:\d+:)?\s(?P<message>.+)$")


def reports(runner: str, linter: str, files: list[str]) -> list[tuple[str, int, str]]:
    """(path, line, message) for each of `linter`'s findings over `files`."""
    if not files:
        return []
    r = subprocess.run(
        [*shlex.split(runner), linter, *files],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if r.returncode not in (0, 1):
        print(
            f"✗ {KEY}: {linter} did not run through '{runner}' (exit {r.returncode}): "
            f"{(r.stderr or r.stdout).strip()[:300]}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    out = []
    for line in r.stdout.splitlines():
        m = REPORT.match(line)
        if m:
            out.append((m["path"], int(m["line"]), m["message"]))
    return out


def unhonoured(found: list[tuple[str, int, str]]) -> list[str]:
    """The findings that sit on a line carrying a `# noqa`: one vocabulary silenced, one not."""
    lines: dict[str, list[str]] = {}
    out = []
    for path, number, message in found:
        if path not in lines:
            try:
                lines[path] = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines[path] = []
        source = lines[path]
        if 0 < number <= len(source) and NOQA.search(source[number - 1]):
            out.append(f"{path}:{number}: {message}  <- `{source[number - 1].strip()}`")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--linters", required=True, help=f"the {KEY} value: linters beside ruff")
    ap.add_argument("--runner", default="python3 -m")
    ap.add_argument(
        "--all", action="store_true", help="every tracked .py under the working directory"
    )
    ap.add_argument("files", nargs="*")
    a = ap.parse_args(argv)
    linters = a.linters.replace(",", " ").split()
    unknown = [n for n in linters if n not in LINTERS]
    if not linters or unknown:
        print(
            f"✗ {KEY}={a.linters!r}: names {', '.join(unknown) or 'no linter'}; "
            f"this reads {', '.join(LINTERS)}",
            file=sys.stderr,
        )
        return 2
    files = a.files
    if a.all:
        listed = subprocess.run(
            ["git", "ls-files", "--", "*.py"],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        files = [f for f in listed.stdout.splitlines() if f]
    bad = []
    for linter in linters:
        for line in unhonoured(reports(a.runner, linter, files)):
            bad.append(f"  {line}\n    a `# noqa` ruff honours and {linter} does not read")
    if bad:
        print(
            f"✗ {len(bad)} line(s) silenced for ruff and still reported by {' and '.join(linters)}:",
            file=sys.stderr,
        )
        print("\n".join(bad), file=sys.stderr)
        print(
            "  fix the code, or run one vocabulary (ruff's F rules, without the second linter)",
            file=sys.stderr,
        )
        return 1
    print(f"✓ no `# noqa` that {' or '.join(linters)} ignores ({len(files)} file(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
