"""`goh kill-by-name` reads the same CODE LINES as the Python checker (Phase N1).

The Python tier strips comments and bare-string statements with `ast` + `tokenize`; the port has a
small Python lexer for the same job (`crates/goh/src/killname/pylex.rs`). Its whole risk is reading
a different set of lines, so every tracked `.py` file in this repository -- a few hundred real
files, docstrings, f-strings and raw regexes included -- is compared line for line. Measured
2026-10-05 over 1,767 `.py` files in 30 local repos: identical after one fix (`rf"\\{{"`: in an
f-string a backslash does not protect a brace).
"""

from __future__ import annotations

import ast
import io
import json
import subprocess
import tokenize
from pathlib import Path

from conftest import REPO_ROOT
from reference_kit import load_reference
from timing_bounds import patience

GROUP = 100
reference = load_reference("check_no_kill_by_name")
# Its whole read of the tree (lib/declared_reads.py JUDGE_READS): every tracked .py file, compared
# by the port it judges (the binary's own tests are chosen by its reach).
JUDGE_READS = ("*.py", "crates/goh/src/killname/")


def _py_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "*.py"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return out.stdout.split()


def _differs(goh: Path, text: str) -> bool:
    want = {str(n): line for n, line in reference.code_lines("a.py", text).items()}
    r = subprocess.run(
        [str(goh), "kill-by-name", "--code-lines", "a.py"],
        input=text.encode(),
        capture_output=True,
        timeout=patience(60),
        check=False,
    )
    return json.loads(r.stdout) != want


def _whole(text: str) -> bool:
    """TEXT can stand among others in one text: it tokenizes, and ends a line. (One that does not
    would send a whole group to the plain line rules, on both sides.)"""
    try:
        list(tokenize.generate_tokens(io.StringIO(text).readline))
        ast.parse(text)
    except (SyntaxError, tokenize.TokenError):
        return False
    return text.endswith("\n")


def test_every_python_file_here_yields_the_same_code_lines(goh: Path) -> None:
    """A lexer reads one text: GROUP files joined are one process where each file was its own
    (1,366 spawns, 12 s of a 14 s test). A group that differs is read again file by file, which
    names the file; a file that cannot be joined is compared alone."""
    files = _py_files()
    assert len(files) > 100, files
    texts = {rel: (REPO_ROOT / rel).read_bytes().decode("utf-8", "replace") for rel in files}
    joined = [rel for rel in files if _whole(texts[rel])]
    assert len(joined) > 0.9 * len(files), sorted(set(files) - set(joined))
    groups = [joined[i : i + GROUP] for i in range(0, len(joined), GROUP)]
    groups += [[rel] for rel in files if rel not in set(joined)]
    differ = []
    for group in groups:
        if _differs(goh, "".join(texts[rel] for rel in group)):
            differ += [r for r in group if _differs(goh, texts[r])] or [f"joined: {group}"]
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
        check=False,
    )
    assert json.loads(r.stdout) == want
