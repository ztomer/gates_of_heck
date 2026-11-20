"""Every gate a consumer's gate.sh runs as a command is executable in git (GOH.GATEEXEC).

install.sh writes each repo's tools/gate.sh, which runs `"$GOH/gates/X.sh" .` as a command, not
through `bash`. goh's own tests run every gate as `bash gates/X.sh`, so a gate committed 100644
passes all of them and is refused only in a consumer's push: gates/cad_gate.sh landed that way on
2026-11-19 ("cad_gate.sh: Permission denied", ZSUS's push refused). The mode is read from the
index, the copy a consumer's ~/.cache/goh/head checkout is made from.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# `"$GOH/gates/name.sh"` in command position: the line's first word, a comment's (`#   "$GOH/...`)
# included, since the template ships those lines commented for the consumer to switch on.
COMMAND = re.compile(r'^\s*(?:#\s*)?"\$GOH/gates/([A-Za-z0-9_]+\.sh)"', re.MULTILINE)


def _entry_points(template: str) -> list[str]:
    return sorted(set(COMMAND.findall(template)))


def _modes() -> dict[str, str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-s", "--", "gates"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=True,
    ).stdout
    return {line.split("\t")[1]: line.split()[0] for line in out.splitlines()}


def test_the_template_is_read_as_commands():
    found = _entry_points((ROOT / "install.sh").read_text())
    assert {"structural.sh", "py_staged.sh", "rust_gate.sh", "cad_gate.sh"} <= set(found), found
    # `exec bash "$GOH/gates/doctor.sh"` runs through bash: not a command-position entry
    assert "doctor.sh" not in found, found


def test_a_sourced_or_bash_run_line_is_not_an_entry_point():
    assert _entry_points('. "$GOH/gates/x.sh"\nexec bash "$GOH/gates/y.sh"\n') == []
    assert _entry_points('"$GOH/gates/z.sh" .\n    #   "$GOH/gates/w.sh" .\n') == ["w.sh", "z.sh"]


def test_every_gate_the_template_runs_is_executable_in_git():
    modes = _modes()
    bad = [
        f"gates/{g}: {modes.get(f'gates/{g}', 'not tracked')}"
        for g in _entry_points((ROOT / "install.sh").read_text())
        if modes.get(f"gates/{g}") != "100755"
    ]
    why = "run as a command by a consumer's gate.sh, so 100755 (git add --chmod=+x):"
    assert bad == [], why + "\n" + "\n".join(bad)
