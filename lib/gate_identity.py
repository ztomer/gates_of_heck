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
    gates root by path, kind and bytes -- ONE formula, so a checkout and its commit's head export
    key alike (GOH.VERDICTKEYDRIFT: two formulas put one tree's loaded verdict under two keys and
    the ship missed it); bytecode skipped;
  * the TOOLS: for each of rustc, cargo, python (the house interpreter), swift, shellcheck, ruff,
    cargo-llvm-cov and xcodebuild, the PATH hit, the binary it resolves to and that binary's size
    and mtime (no spawn: an upgrade rewrites the binary); a rustup proxy also by the toolchain
    binary it resolves to (`rustup which`, not the RUSTUP_TOOLCHAIN spelling); plus the selected
    Xcode. An absent tool is its own token, so installing one changes the identity.
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
    """One line per tool: where PATH finds it and which bytes that is, or `absent`. A rustup proxy
    is keyed by the binary it RESOLVES to (`rustup which`, which reads RUSTUP_TOOLCHAIN and any
    rust-toolchain file the way the gate's own call will), never by the spelling of the override:
    `stable`, `stable-aarch64-apple-darwin` and no override name one rustc, so one identity
    (GOH.VERDICTKEYDRIFT)."""
    out = []
    for name in TOOLS:
        # The interpreter running this is the house one every caller names; keyed by its bytes.
        hit = os.path.realpath(sys.executable) if name == "python3" else shutil.which(name)
        line = f"tool {name} {_fingerprint(hit) if hit else 'absent'}"
        proxy = os.path.realpath(hit) if hit else ""
        if proxy and os.path.basename(proxy) == "rustup":  # a proxy: key what it runs
            got = _run([proxy, "which", name])
            line += f" runs {_fingerprint(got) if os.path.isabs(got) else 'unresolved ' + got}"
        out.append(line)
    xsel = shutil.which("xcode-select")
    out.append(f"xcode {os.environ.get('DEVELOPER_DIR') or (_run([xsel, '-p']) if xsel else '')}")
    return out


def _run(argv: list[str]) -> str:
    r = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False)
    return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else ""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=env,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def _files(root: Path) -> list[str]:
    """The gates' files under root, repo-relative: a checkout's tracked and untracked-unignored
    ones that exist, or every file of an export (a `git archive` of a commit: its tracked files)."""
    if (root / ".git").exists():
        listed = _git(root, "ls-files", "-z", "-co", "--exclude-standard", "--", *PARTS)
        if listed.returncode == 0:
            rels = {r for r in listed.stdout.decode().split("\0") if r}
            return sorted(r for r in rels if os.path.lexists(root / r))
    return sorted(
        str(p.relative_to(root))
        for part in PARTS
        if (root / part).is_dir()
        for p in (root / part).rglob("*")
        if p.is_file() or p.is_symlink()
    )


def content(root: Path) -> str:
    """sha256 of the gates' bytes under root, ONE formula wherever they are read: each file's
    repo-relative path, kind (link, executable, file) and bytes. A checkout and the head export of
    its commit hold the same bytes and key alike; two formulas (`ls-files -s` plus the diff here,
    path and bytes there) put one commit's verdict under two keys (GOH.VERDICTKEYDRIFT)."""
    h = hashlib.sha256()
    for rel in _files(root):
        if "__pycache__" in rel.split("/"):
            continue
        p = root / rel
        if p.is_symlink():
            kind, data = b"l", os.readlink(p).encode()
        else:
            kind, data = (b"x" if p.stat().st_mode & 0o111 else b"f"), p.read_bytes()
        h.update(rel.encode() + b"\0" + kind + b"\0" + str(len(data)).encode() + b"\0" + data)
    return h.hexdigest()


def key(mode: str, root: Path = GOH_ROOT) -> str:
    text = "\n".join(["gate-identity v2", f"mode {mode}", f"content {content(root)}", *tools()])
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
