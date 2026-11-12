"""No shell script under `set -u` expands an array that can be empty with a bare `"${a[@]}"`.

The macOS /bin/bash is 3.2, which calls `"${a[@]}"` of an EMPTY array an unbound variable under
`set -u` (fixed in bash 4.4). goh v0.106.1 shipped `tools/release_goh.sh` with `"${title[@]}"`
after `title=()`: green on Homebrew bash 5, red on the hosted runner's /bin/bash. An array this
file can assign empty (`a=()`) is expanded only as `${a[@]+"${a[@]}"}`, which is nothing when empty
and every element when not, in every bash.
"""

from __future__ import annotations

import re
import subprocess

from conftest import REPO_ROOT

SET_U = re.compile(r"(?m)^\s*set\s+-[a-zA-Z]*u")
EMPTY = re.compile(r"(?m)(?:^|[\s;(])(?:local\s+|declare\s+-a\s+)?([A-Za-z_]\w*)=\(\s*\)")
BARE = re.compile(r'(?<!\+)"\$\{([A-Za-z_]\w*)\[[@*]\]\}"')


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


def test_the_check_sees_the_shape_that_shipped_red():
    bad = 'set -euo pipefail\ntitle=()\nrun "${title[@]}"\n'
    assert findings(bad) == [(3, "title")]
    assert findings(bad.replace('"${title[@]}"', '${title[@]+"${title[@]}"}')) == []
    assert findings(bad.replace("set -euo pipefail\n", "")) == []


def test_no_tracked_script_expands_a_maybe_empty_array_bare():
    files = subprocess.run(
        ["git", "ls-files", "*.sh", "*.bash"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    ).stdout.split()
    bad = [
        f"{f}:{i}: {n}"
        for f in files
        for i, n in findings((REPO_ROOT / f).read_text(encoding="utf-8", errors="replace"))
    ]
    assert not bad, "bare empty-able array under set -u (bash 3.2 unbound):\n" + "\n".join(bad)


def test_bash_32_really_refuses_the_bare_form():
    """Calibration: the macOS /bin/bash is the instrument; prove it goes red on the shape."""
    from pathlib import Path

    if not Path("/bin/bash").exists():
        return
    v = subprocess.run(
        ["/bin/bash", "-c", "echo ${BASH_VERSINFO[0]}"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()
    if v != "3":
        return
    r = subprocess.run(
        ["/bin/bash", "-c", 'set -u; a=(); printf "%s" "${a[@]}"'],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode != 0 and "unbound" in r.stderr
    r = subprocess.run(
        ["/bin/bash", "-c", 'set -u; a=(); printf "%s" ${a[@]+"${a[@]}"}'],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stderr
