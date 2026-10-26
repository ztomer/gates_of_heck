#!/usr/bin/env python3
"""required_tools.py -- which tools a repo's gates refuse without, read from required_tools.tsv.

    gates/required_tools.py --repo .              # the rows for the layers this repo uses
    gates/required_tools.py --repo . --install    # the install commands, grouped
    gates/required_tools.py --layer rust --layer swift --install

    gates/required_tools.py --repo . --names      # bare tool names, for any package manager
    gates/required_tools.py --repo . --check      # red for a tool missing or below its FLOOR
    gates/required_tools.py --check --tool zig    # one tool (what goh_require asks)
    gates/required_tools.py --floors              # tool<TAB>floor, for a consumer's own record

FLOORS (G2b). The manifest's `floor` column is the OLDEST version a gate accepts -- a floor, never
a pin: a newer tool is green, always. `--check` is red, by name and with both versions, for a tool
that is missing, below its floor, or present with a floor but no readable version. `--found TEXT`
judges a version the CALLER read (a module through the gate's own runner).

For a CI workflow: `brew install $(...)` from a hand-kept list drifts the day a gate gains a
requirement -- antiknob's first CI run went red on v0.20.0 for exactly that (shellcheck). Install
from this instead; tests/test_required_tools_manifest.py keeps the manifest equal to the code.
Consume it so a failure here FAILS the step (an `eval "$(...)"` of a failed command runs nothing,
and the step passes having installed nothing):

    set -euo pipefail; cmds="$(python3 "$GOH_DIR/gates/required_tools.py" --repo . --install)"
    grep -v '^#' <<<"$cmds" | bash -eux

On a runner without brew (ubuntu-latest), use `--names` and the runner's own package manager.
`ruff` is its own layer (`python-format`): only repos that set GOH_PYTHON_FORMATTED need it;
`cargo-zigbuild` + `zig` are `rust-cross`, for repos that set GOH_RUST_LINT_CARGO=cargo-zigbuild.

--repo infers layers from what the repo declares (.gatesrc, tools/gate.sh, .githooks/*): a gate
script named there, a `--lang` passed to coverage_gate.sh, `GOH_PYTHON_FORMATTED` set, shell files
tracked. An unknown --layer is refused: a typo that installs nothing reads as a green setup.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "required_tools.tsv")


def rows() -> list[dict]:
    out = []
    with open(MANIFEST, encoding="utf-8") as fh:
        for line in fh:
            if line.strip() and not line.startswith("#"):
                layer, tool, kind, install, site, floor = line.rstrip("\n").split("\t")
                out.append(
                    dict(layer=layer, tool=tool, kind=kind, install=install, site=site, floor=floor)
                )
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
    out = subprocess.run(
        ["git", "-C", repo, "ls-files", "-z"], capture_output=True, stdin=subprocess.DEVNULL
    )
    names = [n.decode("utf-8", "replace") for n in out.stdout.split(b"\0") if n]
    # `goh shell-lint`'s scope (`crates/goh/src/shell_lint.rs::in_scope`): a `*.sh`, or an
    # extensionless git hook under `hooks/` -- a Python hook there is Python.
    return any(
        n.endswith(".sh") or (n.startswith("hooks/") and "." not in n.rsplit("/", 1)[-1])
        for n in names
    )


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
        layers.add("python-format")
    if re.search(r"^\s*(export\s+)?GOH_RUST_LINT_CARGO=['\"]?cargo-zigbuild", text, re.M):
        layers.add("rust-cross")  # the --target lint configs run through cargo-zigbuild
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
    out = []
    if groups["brew"]:
        out.append(f"{prefix['brew']} {' '.join(groups['brew'])}")
    if groups["cargo"]:
        # A cargo tool already on PATH is DONE, whatever put it there. `cargo install` REFUSES a
        # binary it did not record -- `error: binary `cargo-audit` already exists in destination`,
        # exit 101 -- and that is exactly what a CI cache restores: taiki-e/install-action writes
        # ~/.cargo/bin/<tool> with no `.crates.toml`, and Swatinem/rust-cache caches ~/.cargo/bin.
        # Measured 2026-10-14..16: ztools' CI runs these commands under `set -euo pipefail`, so
        # every step after the install was skipped and its gate never ran. This manifest's
        # contract is PRESENCE (the tools a gate REFUSES without), so presence is the check --
        # per tool, because one bare binary must not decide the others' installs.
        names = " ".join(groups["cargo"])
        out.append(
            "for t in " + names + '; do command -v "$t" >/dev/null 2>&1'
            ' || cargo install --locked "$t"; done'
        )
    if groups["pip"]:
        out.append(f"{prefix['pip']} {' '.join(groups['pip'])}")
    return out + lines


# How each tool reports its version, where `<tool> --version` is not it (measured 2026-10-25):
# `zig --version` is a usage error, `cargo-llvm-cov --version` wants its subcommand first, and the
# Python modules are asked through the house interpreter seam, the one the gates run.
PROBES = {
    "zig": ["zig", "version"],
    "swiftlint": ["swiftlint", "version"],
    "cargo-llvm-cov": ["cargo-llvm-cov", "llvm-cov", "--version"],
    "python3": ["bash", os.path.join(HERE, "python.sh"), "-V"],
}
VERSION = re.compile(r"\d+(?:\.\d+)+")


def floors() -> dict[str, str]:
    """tool -> floor, for every tool that has one."""
    return {r["tool"]: r["floor"] for r in rows() if r["floor"] != "-"}


def _probe(row: dict) -> list[str]:
    if row["tool"] in PROBES:
        return PROBES[row["tool"]]
    if row["kind"] == "pip":
        return ["bash", os.path.join(HERE, "python.sh"), "-m", row["tool"], "--version"]
    return [row["tool"], "--version"]


def version_in(text: str) -> str | None:
    match = VERSION.search(text)
    return match.group(0) if match else None


def at_least(found: str, floor: str) -> bool:
    """found >= floor, numerically, field by field ("0.6.24" >= "0.6.9"; "1.0" >= "0.99.1")."""

    def key(text: str) -> list[int]:
        return [int(part) for part in text.split(".")]

    a, b = key(found), key(floor)
    width = max(len(a), len(b))
    return a + [0] * (width - len(a)) >= b + [0] * (width - len(b))


def judge(row: dict, found_text: str | None = None) -> str | None:
    """Why `row`'s tool fails its requirement, or None when it is present and at its floor."""
    tool, floor = row["tool"], row["floor"]
    if found_text is None:
        probe = _probe(row)
        if probe[0] != "bash" and not shutil.which(probe[0]):
            return f"{tool} is not installed -- {row['install']}"
        if floor == "-":
            return None
        try:
            done = subprocess.run(
                probe, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"{tool}: its version cannot be read ({error}); the floor is {floor}"
        found_text = done.stdout + "\n" + done.stderr
    if floor == "-":
        return None
    found = version_in(found_text)
    if found is None:
        return f"{tool}: present, but reports no version the floor ({floor}) can be read against"
    if not at_least(found, floor):
        return (
            f"{tool}: this machine has {found}, the floor is {floor} -- update it ({row['install']}; "
            "the floor's reason is in gates/required_tools.tsv)"
        )
    return None


def check(selected: list[dict], found_text: str | None = None) -> int:
    failures: list[str] = []
    for row in {r["tool"]: r for r in selected}.values():
        reason = judge(row, found_text)
        if reason:
            failures.append(reason)
    for reason in failures:
        print(f"required_tools: {reason}", file=sys.stderr)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--layer", action="append", default=[])
    ap.add_argument("--repo", default=None)
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--names", action="store_true", help="bare tool names, one per line")
    ap.add_argument("--check", action="store_true", help="red for a tool missing or below floor")
    ap.add_argument("--tool", action="append", default=[], help="judge this tool (any layer)")
    ap.add_argument("--found", default=None, help="the version text the caller read (--check)")
    ap.add_argument("--floors", action="store_true", help="tool<TAB>floor for every floor")
    args = ap.parse_args(argv)
    table = rows()
    if args.floors:
        print("\n".join(f"{tool}\t{floor}" for tool, floor in floors().items()))
        return 0
    if args.tool:
        unknown = sorted(set(args.tool) - {r["tool"] for r in table})
        if unknown:
            print(f"required_tools: no manifest row names {', '.join(unknown)}", file=sys.stderr)
            return 2
        if args.check:
            return check([r for r in table if r["tool"] in args.tool], args.found)
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
    if args.check:
        return check(selected)
    if args.names:
        print("\n".join(dict.fromkeys(r["tool"] for r in selected)))
    elif args.install:
        print("\n".join(install_lines(selected)))
    else:
        for r in selected:
            print(f"{r['layer']}\t{r['tool']}\t{r['install']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
