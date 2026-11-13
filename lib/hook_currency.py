"""Which of a repo's stock hooks are STALE: one answer for the estate listing and its refresh
(GOH.HOOKREFRESH).

A hook install.sh copied is stale when its bytes equal a stock copy goh once shipped
(`retired_hooks.sha256`) or the one install.sh recorded writing (`.githooks/.goh-installed/`), and
not today's stock. Those are exactly the hooks install.sh overwrites without --force, so a refresh
that runs install.sh touches only these: a hook a repo edited matches neither and is never
listed, never rewritten. Only `.githooks` is read -- a repo whose core.hooksPath points elsewhere
runs another manager's hooks, which are not goh's to call stale.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

GOH_ROOT = Path(__file__).resolve().parent.parent


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _retired(goh: Path) -> set[tuple[str, str]]:
    """(sha256, hook name) for every stock hook goh ever shipped."""
    out = set()
    try:
        lines = (goh / "retired_hooks.sha256").read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and not line.startswith("#"):
            out.add((parts[0], parts[1]))
    return out


def hooks_path(repo: Path, env: dict | None = None) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), "config", "core.hooksPath"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    return r.stdout.strip() or ".githooks"


def stale(repo: Path, goh: Path = GOH_ROOT, env: dict | None = None) -> list[str]:
    """The names of the repo's stock hooks that are an older stock copy, sorted."""
    hp = hooks_path(repo, env)
    if hp != ".githooks" and Path(hp).resolve() != (repo / ".githooks").resolve():
        return []
    hooks, record = repo / ".githooks", repo / ".githooks" / ".goh-installed"
    retired = _retired(goh)
    found = []
    for stock in sorted((goh / "hooks").iterdir()):
        if not stock.is_file():
            continue
        dst = hooks / stock.name
        if not dst.is_file() or dst.read_bytes() == stock.read_bytes():
            continue
        cur = _sha(dst)
        try:
            recorded = (record / f"{stock.name}.sha256").read_text(encoding="utf-8").strip()
        except OSError:
            recorded = ""
        if cur == recorded or (cur, stock.name) in retired:
            found.append(stock.name)
    return found
