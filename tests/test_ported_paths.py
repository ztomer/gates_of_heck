"""Every failing native step names a source file that exists (R8).

`step_report::ported(source)` is how a native step's failure says where its rule lives. The path is
a hand-written string, and the first module split made one name a file that no longer existed -- a
failure message routing the reader to nothing, which is the R8 defect itself. So every call site is
read here and its path is checked.
"""

from __future__ import annotations

import re

from conftest import REPO_ROOT

CALL = re.compile(r'ported\(\s*"([^"]+)",?\s*\)', re.S)


def test_every_ported_call_names_a_real_source() -> None:
    calls = []
    for path in sorted((REPO_ROOT / "crates" / "goh" / "src").rglob("*.rs")):
        for source in CALL.findall(path.read_text(encoding="utf-8")):
            calls.append((path.name, source))
    assert len(calls) >= 7, calls
    missing = [(where, source) for where, source in calls if not (REPO_ROOT / source).is_file()]
    assert not missing, missing
