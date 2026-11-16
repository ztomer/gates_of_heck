"""The identity of a gate verdict: what besides the tree decided it (GOH.VERDICTKEY).

A verdict recorded by tree alone outlived what made it. The CIP.CACHING survey reproduced two:
gates/proven.sh keyed a step on rustc, cargo, python and swift only, so with another shellcheck
first on PATH it answered "proven ... skipped"; lib/loaded_verdict.py keyed goh-loaded/<tree>.json
on the tree only, so a run green under `--cmd true` answered "green already" for `--cmd false`.
And the release push rule (lib/release_gate.py) trusted a parent's goh-landed line, which a land
under a hand-set GOH_LAND_GATE=true wrote exactly as a full gate's.

    gate_identity.py tools                    # one line per tool a gate step may invoke
    gate_identity.py key --mode MODE [--root GOH]   # sha256 of MODE, the gates' content, the tools

The three parts:
  * MODE -- the command or gate mode the verdict is of (`full` for a land's own push gate, else
    the GOH_LAND_GATE string; a loaded run's command);
  * the CONTENT of the gates that ran: every file under gates/ checks/ lib/ tools/ tui/ of the
    gates root (a checkout's tracked blobs plus its diff, or an export's bytes; bytecode skipped);
  * the TOOLS: for each of rustc, cargo, python (the house interpreter), swift, shellcheck, ruff,
    cargo-llvm-cov and xcodebuild, the PATH hit, the binary it resolves to and that binary's size
    and mtime (no spawn: an upgrade rewrites the binary), plus the selected Xcode and the rustup
    toolchain override. An absent tool is its own token, so installing one changes the identity.
Used by gates/_proven.sh's step identity, lib/loaded_verdict.py's record key and tools/land.sh's
goh-landed line (read by the push rule). Pinned by tests/test_verdict_key.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS = ("rustc", "cargo", "python3", "swift", "shellcheck", "ruff", "cargo-llvm-cov", "xcodebuild")
PARTS = ("gates", "checks", "lib", "tools", "tui")
GOH_ROOT = Path(__file__).resolve().parent.parent


def _fingerprint(path: str) -> str:
    real = os.path.realpath(path)
    try:
        st = os.stat(real)
    except OSError:
        return f"{path} -> {real} unreadable"
    return f"{path} -> {real} {st.st_size} {st.st_mtime_ns}"


def tools() -> list[str]:
    """One line per tool: where PATH finds it and which bytes that is, or `absent`."""
    out = []
    for name in TOOLS:
        # The interpreter running this is the house one every caller names; keyed by its bytes.
        hit = os.path.realpath(sys.executable) if name == "python3" else shutil.which(name)
        line = f"tool {name} {_fingerprint(hit) if hit else 'absent'}"
        if hit and os.path.basename(os.path.realpath(hit)) == "rustup":  # a proxy: ask it
            line += " " + _run([hit, "-V"])
        out.append(line)
    xsel = shutil.which("xcode-select")
    out.append(f"xcode {os.environ.get('DEVELOPER_DIR') or (_run([xsel, '-p']) if xsel else '')}")
    out.append(f"rustup-toolchain {os.environ.get('RUSTUP_TOOLCHAIN', '')}")
    return out


def _run(argv: list[str]) -> str:
    r = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False)
    return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else ""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=env,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def content(root: Path) -> str:
    """sha256 of the gates' bytes under root: tracked blobs and the diff, or an export's files."""
    h = hashlib.sha256()
    staged = _git(root, "ls-files", "-s", "--", *PARTS)
    if staged.returncode == 0 and (root / ".git").exists():
        h.update(staged.stdout)
        h.update(_git(root, "diff", "HEAD", "--binary", "--", *PARTS).stdout)
        others = _git(root, "ls-files", "--others", "--exclude-standard", "--", *PARTS).stdout
        for rel in sorted(others.decode().splitlines()):
            if "__pycache__" not in rel.split("/"):
                h.update(rel.encode() + b"\0" + (root / rel).read_bytes())
        return h.hexdigest()
    for part in PARTS:
        for p in sorted((root / part).rglob("*")) if (root / part).is_dir() else []:
            if p.is_file() and "__pycache__" not in p.parts:
                h.update(str(p.relative_to(root)).encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def key(mode: str, root: Path = GOH_ROOT) -> str:
    text = "\n".join(["gate-identity v1", f"mode {mode}", f"content {content(root)}", *tools()])
    return hashlib.sha256(text.encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("tools")
    k = sub.add_parser("key")
    k.add_argument("--mode", required=True)
    k.add_argument("--root", type=Path, default=GOH_ROOT)
    a = ap.parse_args(argv)
    if a.verb == "tools":
        print("\n".join(tools()))
        return 0
    print(key(a.mode, a.root.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
