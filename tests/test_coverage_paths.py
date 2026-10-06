"""A path the coverage gate is given means what it meant where it was given.

ztools, 2026-10-06: `--floors-json tools/coverage_floors.jsonc` passed the wrapper's `-f` check
from the repo root, then the rust mode `cd`s into the project (`rust/`) and the merger refused it
-- "cannot read floors file", naming neither the cd nor the fix. A relative path is made absolute
before any directory change. (An `exempt` KEY is a different thing: it names a file the way lcov
does, relative to the PROJECT, and is documented that way.)
"""

import json
from pathlib import Path

from test_coverage_gate import LIB_FULLY_COVERED, _mk_crate, run_gate


def test_a_relative_floors_file_is_read_from_where_it_was_named(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    crate = _mk_crate(root, LIB_FULLY_COVERED)  # root/covfix: a project one level down
    (root / "tools").mkdir()
    (root / "tools" / "floors.json").write_text(json.dumps({"file_floor": 100.0, "exempt": {}}))
    r = run_gate(root, "--lang", "rust", "--floors-json", "tools/floors.json", crate.name)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "cannot read floors file" not in r.stdout + r.stderr
