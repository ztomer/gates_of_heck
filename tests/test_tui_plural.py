"""GOH.PLURALHEDGE: a count meets its noun in the tui lib, so no string needs a `(s)` hedge.

`counted(n, noun[, plural])` is `1 file` / `0 files` / `3 files`; `noun(n, ...)` is the word alone,
for a sentence that places the number itself. The shell lib's `counted` and `plural_noun` and the
Rust `goh_tui::counted` / `goh_tui::noun` say the same thing (crates/goh-tui/src/tests.rs).
"""

from __future__ import annotations

import subprocess

import pytest
from conftest import REPO_ROOT

from tui.lib import counted, noun


@pytest.mark.parametrize(
    ("n", "args", "want"),
    [
        (1, ("file",), "1 file"),
        (0, ("file",), "0 files"),
        (3, ("file",), "3 files"),
        (1, ("entry", "entries"), "1 entry"),
        (2, ("entry", "entries"), "2 entries"),
        (2, ("process", "processes"), "2 processes"),
    ],
)
def test_python_counted(n: int, args: tuple[str, ...], want: str) -> None:
    assert counted(n, *args) == want
    assert noun(n, *args) == want.split(" ", 1)[1]


@pytest.mark.parametrize(
    ("call", "want"),
    [
        ("counted 1 file", "1 file"),
        ("counted 0 file", "0 files"),
        ("counted 7 entry entries", "7 entries"),
        ("plural_noun 1 run", "run"),
        ("plural_noun 2 run", "runs"),
        ('counted "$(printf "  3")" step', "3 steps"),
    ],
)
def test_shell_counted(call: str, want: str) -> None:
    r = subprocess.run(
        ["bash", "-c", f'. "{REPO_ROOT}/tui/lib.sh"; {call}'],
        capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert r.stdout == want, r.stdout
