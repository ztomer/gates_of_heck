"""Every failing ported step names files that exist (R8, Phase N1).

`step_report::ported(source, reference)` is how a native step's failure says where its rule lives:
the Rust source and the Python checker it ports. Both are hand-written strings, and the first module
split made one of them name a file that no longer existed -- a failure message routing the reader to
nothing, which is the R8 defect itself. So every call site is read here and both paths are checked.
"""

from __future__ import annotations

import re

from conftest import REPO_ROOT

CALL = re.compile(r'ported\(\s*"([^"]+)",\s*"([^"]+)",?\s*\)', re.S)


def test_every_ported_call_names_a_real_source_and_a_real_reference() -> None:
    calls = []
    for path in sorted((REPO_ROOT / "crates" / "goh" / "src").rglob("*.rs")):
        for source, reference in CALL.findall(path.read_text(encoding="utf-8")):
            calls.append((path.name, source, reference))
    assert len(calls) >= 7, calls
    missing = [
        (where, source, reference)
        for where, source, reference in calls
        if not (REPO_ROOT / source).is_file() or not (REPO_ROOT / "checks" / reference).is_file()
    ]
    assert not missing, missing
