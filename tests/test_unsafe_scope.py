"""`unsafe` lives in ONE file of the workspace, and it is the FFI crate's (rust-no-suppression).

The workspace denies `unsafe_code`; `crates/goh-sys` opts out because it is the libc boundary the
native step wrapper needs (`sigaction` to keep an ignored signal ignored, `killpg`). This test is
the allowlist: `unsafe` in any other Rust file fails, and the listed file losing its last `unsafe`
fails too -- an allowlist entry nothing needs is permission nobody asked for.
"""

from __future__ import annotations

import re

from conftest import REPO_ROOT

ALLOWED = {"crates/goh-sys/src/lib.rs": "the libc boundary: sigaction query, killpg, getrusage"}
UNSAFE = re.compile(r"\bunsafe\b")


def _code(line: str) -> str:
    line = line.split("//", 1)[0]
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', line)


def _files_with_unsafe() -> set[str]:
    found = set()
    for path in (REPO_ROOT / "crates").rglob("*.rs"):
        if "target" in path.parts:
            continue
        if any(
            UNSAFE.search(_code(line)) for line in path.read_text(encoding="utf-8").splitlines()
        ):
            found.add(str(path.relative_to(REPO_ROOT)))
    return found


def test_unsafe_only_where_the_allowlist_says() -> None:
    assert _files_with_unsafe() - set(ALLOWED) == set()


def test_every_allowlisted_file_still_needs_it() -> None:
    assert set(ALLOWED) - _files_with_unsafe() == set()
