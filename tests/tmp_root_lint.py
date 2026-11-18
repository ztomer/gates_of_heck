"""The temp roots goh's own code may not make outside `$TMPDIR/goh.noindex` (GOH.TMPNOINDEX).

Spotlight indexed goh's pytest temp trees: 94% (337/359) of the paths mds resolved in 30 s of gates
(2026-11-17, EST.MDSCRAWL). It never indexes under a directory named `*.noindex`, so every root
lives under ONE, through one helper: `goh_tmp` (lib/tmp_root.sh) or `goh_tmp()` (lib/tmp_root.py).

What this READS, in every `*.sh` and `*.py` under gates/, lib/ and tools/:

  * a shell `mktemp` whose template is in the temp directory -- no template at all, `-t`, or one
    naming `TMPDIR` or `/tmp` -- unless the template is `$(goh_tmp)/...`;
  * a Python `tempfile.mkdtemp`/`mkstemp`/`TemporaryDirectory`/`NamedTemporaryFile` with no `dir=`.

What it does NOT read, so a reader knows the hole: a temp path built by hand (`$TMPDIR/x` then
`mkdir`), a `dir=` that itself names the bare temp directory, and a comment line's `mktemp`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SCOPE = ("gates", "lib", "tools")
HELPER = "$(goh_tmp)"
MAKERS = {"mkdtemp", "mkstemp", "TemporaryDirectory", "NamedTemporaryFile"}
CALL = re.compile(r"(?:^|[\s(`;|&])mktemp(?=[\s)`;]|$)(.*)")
IN_TEMP = re.compile(r"TMPDIR|^/tmp(/|$)|^/private/tmp(/|$)")


def shell_findings(text: str) -> list[int]:
    """The 1-based lines whose `mktemp` makes a root in the temp directory, not under goh_tmp."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        m = CALL.search(line)
        if m and _bypasses(m.group(1)):
            out.append(n)
    return out


def _bypasses(rest: str) -> bool:
    flags, template = [], ""
    for word in rest.split():
        if word.startswith("-"):
            flags.append(word)
            continue
        template = word.strip("\"'")
        break
    if template.startswith(HELPER):
        return False
    no_template = not template or template[0] in ")`;|&"
    named_dir = any(re.match(r"-[a-zA-Z]*t", f) for f in flags)
    return no_template or named_dir or bool(IN_TEMP.search(template))


def python_findings(text: str) -> list[int]:
    """The lines of a tempfile maker called with no `dir=`: the bare temp directory."""
    out = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
        if name in MAKERS and not any(k.arg == "dir" for k in node.keywords):
            out.append(node.lineno)
    return out


def findings(root: Path) -> list[str]:
    """`path:line` for every bypass in the scope."""
    out = []
    for top in SCOPE:
        for p in sorted((root / top).rglob("*")):
            if p.suffix == ".sh":
                lines = shell_findings(p.read_text(errors="replace"))
            elif p.suffix == ".py":
                lines = python_findings(p.read_text(errors="replace"))
            else:
                continue
            out += [f"{p.relative_to(root)}:{n}" for n in lines]
    return out
