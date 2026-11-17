"""A proven step is keyed on the goh files that can decide IT (GOH.PROVENCLOSURE): a ship that
changes one scanner misses only the steps whose closure holds it; a step the reader cannot place
(it dispatches the goh binary) keys on the whole content, as every step did before."""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import step_closure as gi

STEPS = (
    'python3 "$GOH_DIR/checks/check_a.py"',
    'python3 "$GOH_DIR/checks/check_b.py"',
    "cargo test --locked",
    "python3 tools/mine.py",
    '"$GOH_DIR/gates/disp.sh" status',
)


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def _lines(goh: Path, repo: Path) -> dict[str, str]:
    gi._hashed.cache_clear()
    gi.runner_closure.cache_clear()
    gi._code.cache_clear()
    gi.candidates.cache_clear()
    out = gi.closures([goh], list(STEPS), None, repo)
    return {ln.split("\t", 1)[1]: ln.split("\t", 1)[0] for ln in out[1:]}


def test_a_one_scanner_change_misses_only_the_steps_that_run_it(tmp_path) -> None:
    goh, repo = tmp_path / "goh", tmp_path / "repo"
    _write(
        goh,
        {
            "gates/local_ci.sh": "#!/usr/bin/env bash\n"
            '. "$(dirname "${BASH_SOURCE[0]}")/_run.sh"\n',
            "gates/proven.sh": "#!/usr/bin/env bash\n",
            "gates/_run.sh": "# shellcheck shell=bash\nrun() { :; }\n",
            "gates/disp.sh": '#!/usr/bin/env bash\nexec "$goh_native" "$@"\n',
            "checks/check_a.py": "import importlib\nm = importlib.import_module('_scan_a')\n",
            "checks/_scan_a.py": "A = 1\n",
            "checks/check_b.py": "TABLE = ROOT / 'lib' / 'table.json'\n",
            "lib/table.json": "{}\n",
            "lib/unrelated.py": "X = 1\n",
        },
    )
    _write(repo, {"tools/mine.py": "from tui.lib import ok\n", "tui/.keep": ""})
    _write(goh, {"tui/lib.py": "def ok(): pass\n"})
    before = _lines(goh, repo)
    assert before[STEPS[4]].startswith("closure full"), before

    # A change to the scanner check_a loads by name: check_a's step misses, no other closure step.
    (goh / "checks/_scan_a.py").write_text("A = 2\n")
    after = _lines(goh, repo)
    moved = {s for s in STEPS if after[s] != before[s]}
    assert moved == {STEPS[0], STEPS[4]}, moved

    # A data file check_b names in code: its step misses.
    (goh / "lib/table.json").write_text('{"x": 1}\n')
    again = _lines(goh, repo)
    assert {s for s in STEPS if again[s] != after[s]} == {STEPS[1], STEPS[4]}

    # The runner's own closure is in every step's.
    (goh / "gates/_run.sh").write_text("# shellcheck shell=bash\nrun() { true; }\n")
    last = _lines(goh, repo)
    assert {s for s in STEPS if last[s] != again[s]} == set(STEPS)

    # A goh module the consumer's script imports is in its closure.
    (goh / "tui/lib.py").write_text("def ok(): return 1\n")
    tui = _lines(goh, repo)
    assert {s for s in STEPS if tui[s] != last[s]} == {STEPS[3], STEPS[4]}
