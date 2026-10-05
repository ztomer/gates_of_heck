#!/usr/bin/env python3
"""required_tools.py -- which tools a repo's gates refuse without, read from required_tools.tsv.

    gates/required_tools.py --repo .              # the rows for the layers this repo uses
    gates/required_tools.py --repo . --install    # the install commands, grouped
    gates/required_tools.py --layer rust --layer swift --install

For a CI workflow: `brew install $(...)` from a hand-kept list drifts the day a gate gains a
requirement -- antiknob's first CI run went red on v0.20.0 for exactly that (shellcheck). Install
from this instead; tests/test_required_tools_manifest.py keeps the manifest equal to the code.

--repo infers layers from what the repo declares (.gatesrc, tools/gate.sh, .githooks/*): a gate
script named there, a `--lang` passed to coverage_gate.sh, `GOH_PYTHON_FORMATTED` set, shell files
tracked. An unknown --layer is refused: a typo that installs nothing reads as a green setup.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "required_tools.tsv")


def rows() -> list[dict]:
    out = []
    with open(MANIFEST, encoding="utf-8") as fh:
        for line in fh:
            if line.strip() and not line.startswith("#"):
                layer, tool, kind, install, site = line.rstrip("\n").split("\t")
                out.append(dict(layer=layer, tool=tool, kind=kind, install=install, site=site))
    return out


def _declared(repo: str) -> str:
    text = []
    for rel in (".gatesrc", "tools/gate.sh"):
        try:
            with open(os.path.join(repo, rel), encoding="utf-8", errors="replace") as fh:
                text.append(fh.read())
        except OSError:
            pass
    hooks = os.path.join(repo, ".githooks")
    if os.path.isdir(hooks):
        for name in sorted(os.listdir(hooks)):
            path = os.path.join(hooks, name)
            if os.path.isfile(path):
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text.append(fh.read())
    return "\n".join(text)


def _tracks_shell(repo: str) -> bool:
    out = subprocess.run(["git", "-C", repo, "ls-files", "-z"], capture_output=True)
    names = [n.decode("utf-8", "replace") for n in out.stdout.split(b"\0") if n]
    return any(n.endswith(".sh") or n.startswith("hooks/") for n in names)


def detect(repo: str) -> tuple[set[str], set[str]]:
    """(layers, structural tools) this repo's declared gates need."""
    text = _declared(repo)
    layers = set()
    for gate, layer in (
        ("rust_gate.sh", "rust"),
        ("swift_gate.sh", "swift"),
        ("py_gate.sh", "python"),
        ("py_staged.sh", "python"),
        ("check_no_screen_linkage", "screen-linkage"),
    ):
        if gate in text:
            layers.add(layer)
    for lang in re.findall(r"coverage_gate\.sh[^\n]*--lang[ =](\w+)", text):
        layers.add(f"coverage-{lang}")
    if "rust" in layers and re.search(r"^\s*(export\s+)?GOH_COV_FLOOR", text, re.M):
        layers.add("coverage-rust")  # rust_gate.sh runs the coverage step itself
    structural = set()
    if _tracks_shell(repo):
        structural.add("shellcheck")
    if re.search(r"^\s*(export\s+)?GOH_PYTHON_FORMATTED=\S", text, re.M):
        structural.add("ruff")
    return layers, structural


def install_lines(selected: list[dict]) -> list[str]:
    groups: dict[str, list[str]] = {"brew": [], "cargo": [], "pip": []}
    lines = []
    for r in selected:
        if r["kind"] in groups:
            pkg = r["install"].split()[-1]
            if pkg not in groups[r["kind"]]:
                groups[r["kind"]].append(pkg)
        elif f"# needs {r['tool']}: {r['install']}" not in lines:
            lines.append(f"# needs {r['tool']}: {r['install']}")
    prefix = {
        "brew": "brew install",
        "cargo": "cargo install --locked",
        "pip": "python3 -m pip install",
    }
    return [f"{prefix[k]} {' '.join(v)}" for k, v in groups.items() if v] + lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--layer", action="append", default=[])
    ap.add_argument("--repo", default=None)
    ap.add_argument("--install", action="store_true")
    args = ap.parse_args(argv)
    table = rows()
    known = {r["layer"] for r in table}
    bad = [layer for layer in args.layer if layer not in known]
    if bad:
        print(
            f"required_tools: unknown layer(s) {', '.join(bad)}; known: {', '.join(sorted(known))}",
            file=sys.stderr,
        )
        return 2
    layers, structural = set(args.layer), set()
    if args.repo:
        found, structural = detect(args.repo)
        layers |= found
    selected = [r for r in table if r["layer"] in layers and r["layer"] != "structural"]
    selected += [
        r
        for r in table
        if r["layer"] == "structural" and (r["tool"] in structural or "structural" in args.layer)
    ]
    if args.install:
        print("\n".join(install_lines(selected)))
    else:
        for r in selected:
            print(f"{r['layer']}\t{r['tool']}\t{r['install']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
