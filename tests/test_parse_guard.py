"""Every bash script a consumer EXECUTES from this checkout parses whole before it runs.

Measured 2026-10-05: routines' pre-push died with `coverage_gate.sh: line 485: syntax error near
unexpected token ';;'` -- a line that is not a syntax error. bash reads a script LAZILY, by byte
offset, as it executes; this checkout was edited while the script ran, every unread offset moved,
and bash resumed mid-token. Every consumer delegates here at runtime (GOH_DIR), so any edit, pull or
checkout in this repo can break any consumer's gate that happens to be running.

The guard is a brace group around the whole body: bash must read a compound command to its closing
`}` before executing any of it, so the run is pinned to the bytes it started with. Sourced files
need nothing -- `source` reads the whole file before executing it. `tools/release-kit/release.sh`
solves the same problem by re-exec'ing a temp copy of itself, which it can afford because it does
not resolve its siblings from its own path; the gates all do.
"""

import re
import subprocess
import time
from pathlib import Path

from conftest import REPO_ROOT

OPEN = "{ # parse-guard"
CLOSE = "} # parse-guard"


def _tracked_bash() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.split()
    return [
        f
        for f in out
        if (f.endswith(".sh") or f.startswith("hooks/"))
        and not f.startswith(("tests/", "tools/profiling/"))
        and (REPO_ROOT / f)
        .read_text(errors="replace")
        .startswith(("#!/usr/bin/env bash", "#!/bin/bash"))
    ]


def _sourced(files: list[str]) -> set[str]:
    """Basenames some tracked script SOURCES (`. x` / `source x`): those must stay unguarded, since
    the closing `exit` would end the shell that sourced them."""
    names = set()
    for f in files:
        for line in (REPO_ROOT / f).read_text(errors="replace").splitlines():
            m = re.match(r"\s*(?:\.|source)\s+\"?([^\"\s;]+)", line)
            if m:
                names.add(Path(m.group(1)).name)
    return names


def executed_scripts() -> list[str]:
    files = _tracked_bash()
    sourced = _sourced(files)
    return [
        f
        for f in files
        if Path(f).name not in sourced
        and not Path(f).name.startswith("_")
        and f not in ("tools/release-kit/release.sh",)  # buffered by its own re-exec copy
        and not f.startswith("lib/")  # sourced by consumers, not by this tree
    ]


def test_the_scope_is_not_empty():
    names = executed_scripts()
    assert "gates/push_gate.sh" in names and "gates/coverage_gate.sh" in names, names
    assert "gates/_common.sh" not in names and "tui/lib.sh" not in names, names


def test_every_executed_script_is_parse_guarded():
    bad = []
    for f in executed_scripts():
        lines = [l for l in (REPO_ROOT / f).read_text().splitlines() if l.strip()]
        code = [l for l in lines[1:] if not l.lstrip().startswith("#")]
        if not code or not code[0].startswith(OPEN) or lines[-1] != CLOSE or lines[-2] != "exit":
            bad.append(f)
    assert not bad, f"not parse-guarded (see this file's docstring): {bad}"


def test_a_guarded_script_survives_being_rewritten_mid_run(tmp_path):
    """The mechanism, calibrated both ways: unguarded, the rewrite changes what runs."""
    body = 'sleep 1\necho "ORIGINAL"\n'
    later = "\n" * 7 + 'echo "REWRITTEN"\n'

    def run(script: str) -> str:
        path = tmp_path / "s.sh"
        path.write_text(script)
        proc = subprocess.Popen(["/bin/bash", str(path)], stdout=subprocess.PIPE, text=True)
        time.sleep(0.4)
        path.write_text("#!/bin/bash\n" + later * 3)
        out, _ = proc.communicate(timeout=10)
        return out

    guarded = run(f"#!/bin/bash\n{OPEN}\n{body}exit\n{CLOSE}\n")
    assert guarded.strip() == "ORIGINAL", guarded
    unguarded = run(f"#!/bin/bash\n{body}")
    assert unguarded.strip() != "ORIGINAL", "the calibration arm cannot fail: the rewrite never bit"
