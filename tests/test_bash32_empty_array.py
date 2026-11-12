"""The bash 3.2 trap, re-pointed at what the bash 5 guard does NOT cover (GOH.BASH5).

The macOS /bin/bash is 3.2, which calls `"${a[@]}"` of an EMPTY array an unbound variable under
`set -u` (fixed in bash 4.4); goh v0.106.1 shipped `tools/release_goh.sh` green on Homebrew bash 5
and red on the hosted runner's /bin/bash. v0.106.2 rewrote 18 sites as `${a[@]+"${a[@]}"}`.

Since the guard (lib/bash5.sh, tests/test_bash5_guard.py) runs first in every EXECUTED script, an
executed script is run by bash 5 and those 18 workarounds are gone. Two things stay:

* the PROOF, against the real instrument: under /bin/bash 3.2 the bare form dies, and the same
  bare form behind the guard runs (test_the_guard_is_what_makes_the_bare_form_safe);
* the RULE for files the guard does not re-exec -- SOURCED libraries, which consumer scripts load
  under whatever bash they run: an array such a file can assign empty is expanded only as
  `${a[@]+"${a[@]}"}`.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from test_bash5_guard import _scope

SET_U = re.compile(r"(?m)^\s*set\s+-[a-zA-Z]*u")
EMPTY = re.compile(r"(?m)(?:^|[\s;(])(?:local\s+|declare\s+-a\s+)?([A-Za-z_]\w*)=\(\s*\)")
BARE = re.compile(r'(?<!\+)"\$\{([A-Za-z_]\w*)\[[@*]\]\}"')
SOURCE = re.compile(r"\s*(?:\.|source)\s+\"?([^\"\s;]+)")


def findings(text: str) -> list[tuple[int, str]]:
    if not SET_U.search(text):
        return []
    empty = set(EMPTY.findall(text))
    return [
        (i, n)
        for i, line in enumerate(text.splitlines(), 1)
        for n in BARE.findall(line)
        if n in empty
    ]


def sourced_files() -> list[str]:
    """The guarded files the guard never re-execs: no bash shebang, or sourced by a tracked file."""
    files = _scope()
    names = {
        Path(m.group(1)).name
        for f in files
        for line in (REPO_ROOT / f).read_text(errors="replace").splitlines()
        if (m := SOURCE.match(line))
    }
    return [
        f
        for f in files
        if Path(f).name in names or not (REPO_ROOT / f).read_text(errors="replace").startswith("#!")
    ]


def test_the_check_sees_the_shape_that_shipped_red():
    bad = 'set -euo pipefail\ntitle=()\nrun "${title[@]}"\n'
    assert findings(bad) == [(3, "title")]
    assert findings(bad.replace('"${title[@]}"', '${title[@]+"${title[@]}"}')) == []
    assert findings(bad.replace("set -euo pipefail\n", "")) == []


def test_the_scope_is_the_sourced_libraries():
    names = sourced_files()
    assert "tui/lib.sh" in names and "gates/_common.sh" in names, names
    assert "tools/release_goh.sh" not in names and "gates/push_gate.sh" not in names, names


def test_no_sourced_library_expands_a_maybe_empty_array_bare():
    bad = [
        f"{f}:{i}: {n}"
        for f in sourced_files()
        for i, n in findings((REPO_ROOT / f).read_text(encoding="utf-8", errors="replace"))
    ]
    assert not bad, "bare empty-able array in a sourced library (bash 3.2 unbound):\n" + "\n".join(
        bad
    )


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False,
                          stdin=subprocess.DEVNULL, timeout=60)  # fmt: skip


def test_the_guard_is_what_makes_the_bare_form_safe(tmp_path):
    """Calibration: /bin/bash 3.2 is the instrument. Unguarded, the bare form dies there; guarded,
    the same body runs, because bash 5 runs it."""
    if not Path("/bin/bash").exists():
        pytest.skip("no /bin/bash")
    v = _run(["/bin/bash", "-c", "echo ${BASH_VERSINFO[0]}"]).stdout.strip()
    if v != "3":
        pytest.skip(f"/bin/bash is bash {v}, not the 3.2 this calibrates against")
    body = 'set -u\na=()\nprintf "%s" "${a[@]}"\necho "ran on ${BASH_VERSINFO[0]}"\n'
    bare = tmp_path / "bare.sh"
    bare.write_text("#!/usr/bin/env bash\n" + body)
    r = _run(["/bin/bash", str(bare)])
    assert r.returncode != 0 and "unbound" in r.stderr, (r.stdout, r.stderr)
    guarded = tmp_path / "guarded.sh"
    guarded.write_text(
        f'#!/usr/bin/env bash\n. "{REPO_ROOT}/lib/bash5.sh" && goh_bash5 "$@"\n' + body
    )
    r = _run(["/bin/bash", str(guarded)])
    assert r.returncode == 0 and r.stdout.strip() == "ran on 5", (r.stdout, r.stderr)
