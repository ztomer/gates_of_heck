"""`goh kill-by-name` reads the same CODE LINES as the Python checker (Phase N1).

The Python tier strips comments and bare-string statements with `ast` + `tokenize`; the port has a
small Python lexer for the same job (`crates/goh/src/killname/pylex.rs`). Its whole risk is reading
a different set of lines, so every tracked `.py` file in this repository -- a few hundred real
files, docstrings, f-strings and raw regexes included -- is compared line for line. Measured
2026-10-05 over 1,767 `.py` files in 30 local repos: identical after one fix (`rf"\\{{"`: in an
f-string a backslash does not protect a brace).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT

from reference_kit import load_reference  # noqa: E402

reference = load_reference("check_no_kill_by_name")


def _py_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "*.py"], capture_output=True, text=True
    )
    return out.stdout.split()


def test_every_python_file_here_yields_the_same_code_lines(goh: Path) -> None:
    files = _py_files()
    assert len(files) > 100, files
    differ = []
    for rel in files:
        text = (REPO_ROOT / rel).read_bytes().decode("utf-8", "replace")
        want = {str(n): line for n, line in reference.code_lines(rel, text).items()}
        r = subprocess.run(
            [str(goh), "kill-by-name", "--code-lines", rel],
            input=text,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if json.loads(r.stdout) != want:
            differ.append(rel)
    assert not differ, differ


def test_an_untokenizable_file_falls_back_on_both(goh: Path) -> None:
    """An unclosed bracket: `tokenize` raises, so both read the plain line rules."""
    text = "def (:\n    x = 1  # trailing\n"
    want = {str(n): line for n, line in reference.code_lines("b.py", text).items()}
    r = subprocess.run(
        [str(goh), "kill-by-name", "--code-lines", "b.py"],
        input=text,
        capture_output=True,
        text=True,
    )
    assert json.loads(r.stdout) == want
