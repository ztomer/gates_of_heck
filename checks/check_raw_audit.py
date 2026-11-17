#!/usr/bin/env python3
"""check_raw_audit.py -- no repo runs `cargo audit` outside goh's advisory-db wrapper.

    check_raw_audit.py [--empty-ok] [ROOT]  # judge the repo at ROOT (default: here)
    check_raw_audit.py --estate DIR    # judge every repo directly under DIR; ratchet the seed
    check_raw_audit.py --probe         # prove THIS gate can go red

WHY (GOH.AUDITONCE, 2026-11-17). goh v0.130.0's pin pushes were refused in antiknob and
divoom-control on RUSTSEC-2026-0543 (chrono via rmcp), the advisory goh's shared ruling covers
(gates/shared_advisories.toml): rust_gate.sh's audit passed, and a SECOND audit the repo ran
itself -- a raw `cargo audit` in tools/repo_gates.sh, a step in GOH_CI_STEPS -- judged the same
lock without the ruling. Five consumers carried one. A raw audit also refreshes the SHARED
`~/.cargo/advisory-db` under every other audit's reads (the 2026-10-12 tear). The wrapper,
`python3 "$GOH_DIR/lib/advisory_db.py" run --fetch|--no-fetch -- cargo audit ...`, applies the
shared rulings per lock, reads goh's own locked copy, and bounds the run (GOH.AUDITSPIN).

WHAT. Every step of `.gatesrc`'s GOH_CI_STEPS (sourced as the runner sources it) and every tracked
script under `tools/`: a shell COMMAND (comments and quoted text are not commands; a line naming
`cargo audit` in a message is not one) whose program is `cargo` and whose subcommand is `audit`,
or, in a Python file that never names `advisory_db`, an argv list `["cargo", "audit", ...]`.

THE SEED (checks/raw_audit_seed.json, `{"repo": {"path": "reason"}}`): today's violations, so
the gate landed green. A seeded finding is exempt in its repo; `--estate` refuses an entry that no
longer fires (a stale excuse is permission nobody needs). Done is an empty seed.

Exit 0 clean, 1 a raw audit (or a stale seed entry), 2 nothing to judge (no GOH_CI_STEPS and no
tracked tools/ script, or a tree tracking only shape files: a scan of nothing is not clean).
`--empty-ok` is rust_gate.sh's call, where a crate with neither has nothing that could run an
audit of its own; the sweep (check_empty_scope.py) runs it without.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(HERE.parent))
from _gitutil import foreign_repo_env  # noqa: E402
from gate_coverage import _commands, _logical_lines, ci_steps  # noqa: E402

from tui.lib import err, ok, warn  # noqa: E402

NAME = "raw_audit"
SEED = HERE / "raw_audit_seed.json"
SHELL_SUFFIXES = (".sh", ".bash", ".zsh")
PY_ARGV = re.compile(r"""["']cargo["']\s*,\s*(?:["']\+[^"']*["']\s*,\s*)?["']audit["']""")
PREFIX_WORDS = {"if", "then", "do", "else", "elif", "!", "exec", "time", "command", "env"}
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
TAGS = re.compile(r"^\s*\[[^\]]*\]\s*")
WRAPPER = 'python3 "$GOH_DIR/lib/advisory_db.py" run --fetch -- cargo audit ...'


def _raw(words: list[str]) -> bool:
    """One shell command: is its program `cargo` running `audit`?"""
    i = 0
    while i < len(words) and (words[i] in PREFIX_WORDS or ASSIGN.match(words[i])):
        i += 1
    rest = words[i:]
    if len(rest) > 1 and rest[1].startswith("+"):
        rest = [rest[0], *rest[2:]]
    return len(rest) > 1 and os.path.basename(rest[0]) == "cargo" and rest[1] == "audit"


def raw_in_shell(text: str) -> bool:
    return any(_raw(c) for line in _logical_lines(text) for c in _commands(line))


# The files that give a tree its shape (checks/check_empty_scope.py's SHAPE_FILES): a tree tracking
# only these has no repo to be right about, though its .gatesrc still lists steps.
SHAPE_FILES = {
    "Package.swift",
    "Makefile",
    "pyproject.toml",
    "Cargo.toml",
    "Cargo.lock",
    "CMakeLists.txt",
    ".gatesrc",
    "package.json",
}


def _tracked(root: Path, *paths: str) -> list[str]:
    r = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", *paths],
        capture_output=True,
        text=True,
        check=False,
        env=foreign_repo_env(),
        stdin=subprocess.DEVNULL,
    )
    return [p for p in r.stdout.split("\0") if p]


def _shell_file(root: Path, rel: str) -> bool:
    if rel.endswith(SHELL_SUFFIXES):
        return True
    if "." in Path(rel).name:
        return False
    try:
        head = (root / rel).read_bytes()[:64]
    except OSError:
        return False
    return head.startswith(b"#!") and any(s in head for s in (b"sh\n", b"bash", b"zsh"))


def findings(root: Path) -> tuple[list[str], int]:
    """(`path: what` per raw audit, how many sources were read)."""
    out: list[str] = []
    env = {k: v for k, v in foreign_repo_env().items() if not k.startswith("GOH_")}
    steps = ci_steps(root, env) if (root / ".gatesrc").is_file() else []
    for n, step in enumerate(steps, start=1):
        if raw_in_shell(TAGS.sub("", step)):
            out.append(f".gatesrc: GOH_CI_STEPS step {n} runs a raw audit: {step.strip()}")
    if not any(p not in SHAPE_FILES for p in _tracked(root)):
        return [], 0
    scripts = _tracked(root, "tools")
    for rel in scripts:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if rel.endswith(".py"):
            if "advisory_db" not in text and any(
                PY_ARGV.search(line) and not line.lstrip().startswith("#")
                for line in text.splitlines()
            ):
                out.append(f"{rel}: runs a raw audit (an argv of cargo audit)")
        elif _shell_file(root, rel) and raw_in_shell(text):
            out.append(f"{rel}: runs a raw audit")
    return out, len(steps) + len(scripts)


def load_seed(path: Path | None = None) -> dict[str, dict[str, str]]:
    f = path or Path(os.environ.get("GOH_RAW_AUDIT_SEED") or SEED)
    raw = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def judge(root: Path, seed: dict[str, dict[str, str]]) -> tuple[list[str], list[str], int]:
    """(unseeded findings, stale seed entries of this repo, sources read)."""
    found, read = findings(root)
    mine = seed.get(root.name, {})
    hit_paths = {f.split(":", 1)[0] for f in found}
    open_ = [f for f in found if f.split(":", 1)[0] not in mine]
    stale = [f"{root.name}/{p}" for p in mine if p not in hit_paths]
    return open_, stale, read


def _report(root: Path, open_: list[str]) -> None:
    err(f"[{NAME}] {root.name}: a `cargo audit` outside goh's advisory-db wrapper")
    for f in open_:
        err(f"  {f}")
    err(
        "  Drop it where rust_gate.sh already audits the same lock (it does, through the wrapper),"
        f" else route it through the wrapper: {WRAPPER}. A raw audit skips goh's shared advisory"
        " rulings (gates/shared_advisories.toml) and refreshes the shared ~/.cargo/advisory-db"
        " under other audits' reads (GOH.AUDITONCE)."
    )


def one(root: Path, empty_ok: bool = False) -> int:
    open_, stale, read = judge(root, load_seed())
    if read == 0 and empty_ok:
        ok(f"[{NAME}] {root.name} has no GOH_CI_STEPS and no tools/ script: nothing runs an audit")
        return 0
    if read == 0:
        err(
            f"[{NAME}] nothing to judge in {root}: no GOH_CI_STEPS and no tracked tools/ script,"
            " or a tree tracking only its shape files -- a scan of nothing is not a clean scan"
        )
        return 2
    for s in stale:
        warn(f"[{NAME}] {s} is seeded but runs no raw audit now: delete it from {SEED}")
    if open_:
        _report(root, open_)
        return 1
    ok(f"[{NAME}] no raw cargo audit in {root.name} ({read} steps and tools/ scripts read)")
    return 0


def estate(top: Path) -> int:
    seed = load_seed()
    repos = sorted(p for p in top.iterdir() if (p / ".git").exists())
    bad = stale_all = 0
    for repo in repos:
        open_, stale, _ = judge(repo, seed)
        if open_:
            bad += 1
            _report(repo, open_)
        for s in stale:
            stale_all += 1
            err(f"[{NAME}] stale seed entry {s}: it runs no raw audit now; delete it from {SEED}")
    missing = sorted(set(seed) - {r.name for r in repos})
    for name in missing:
        stale_all += 1
        err(f"[{NAME}] stale seed entry {name}: no such repo under {top}")
    if bad or stale_all:
        return 1
    ok(f"[{NAME}] {len(repos)} repos under {top}: no raw cargo audit outside the seed")
    return 0


def probe() -> int:
    """A planted raw audit step is red; the same step through the wrapper is green."""
    with tempfile.TemporaryDirectory(prefix="goh-raw-audit-probe.") as tmp:
        root = Path(tmp) / "probe"
        root.mkdir()
        subprocess.run(
            ["git", "init", "-q", str(root)],
            check=True,
            env=foreign_repo_env(),
            stdin=subprocess.DEVNULL,
        )
        (root / "README.md").write_text("probe\n")
        gatesrc = root / ".gatesrc"
        gatesrc.write_text("GOH_CI_STEPS='./gates/rust_gate.sh:cargo audit --deny warnings'\n")
        subprocess.run(
            ["git", "-C", str(root), "add", "-A"],
            check=True,
            env=foreign_repo_env(),
            stdin=subprocess.DEVNULL,
        )
        red = findings(root)[0]
        gatesrc.write_text(
            "GOH_CI_STEPS='python3 \"$GOH_DIR/lib/advisory_db.py\" run --fetch -- cargo audit'\n"
        )
        green = findings(root)[0]
    if red and not green:
        ok(f"[{NAME}] probe: a raw audit step is red, the wrapped one green")
        return 0
    err(f"[{NAME}] probe FAILED: raw={red} wrapped={green}")
    return 1


def main(argv: list[str]) -> int:
    if argv[:1] == ["--probe"]:
        return probe()
    if argv[:1] == ["--estate"] and len(argv) == 2:
        return estate(Path(argv[1]).expanduser().resolve())
    empty_ok = argv[:1] == ["--empty-ok"]
    argv = argv[1:] if empty_ok else argv
    if len(argv) > 1 or (argv and argv[0].startswith("-")):
        print(__doc__, file=sys.stderr)
        return 2
    start = Path(argv[0] if argv else ".").resolve()
    top = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
        env=foreign_repo_env(),
        stdin=subprocess.DEVNULL,
    ).stdout.strip()
    return one(Path(top) if top else start, empty_ok)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
