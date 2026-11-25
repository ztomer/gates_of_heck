"""Which of a repo's stock hooks are STALE, and which of those are REFUSED: the ONE reading every
asker uses (GOH.HOOKREFRESH, GOH.HOOKSWEEP, GOH.HOOKCURRENCYDUP).

A hook install.sh copied is STALE when its bytes equal a stock copy goh once shipped
(`retired_hooks.sha256`) or the one install.sh recorded writing (`.githooks/.goh-installed/`), and
not today's stock. Those are exactly the hooks install.sh overwrites without --force, so a refresh
that runs install.sh touches only these: a hook a repo edited matches neither and is never
listed, never rewritten. Only `.githooks` is read -- a repo whose core.hooksPath points elsewhere
runs another manager's hooks, which are not goh's to call stale.

A stale hook is REFUSED when it carries gate logic of its own: some `exec` in it does not run a
script of the shared checkout's `gates/` (2fb2901's pre-commit ran the repo's gate itself), so an
old copy does not run today's gate. A retired entry tagged `same-logic` retired only text, so it is
never refused (BACKLOG 6.19). Every other stale hook DELEGATES -- it execs commit_gate.sh,
push_gate.sh, commit_msg.sh or sequencer_gate.sh, read at run time -- and is AGING: named with
`goh hooks --refresh`, never refused. Judged by the hook's BYTES, never by a tag.

Since GOH.CLASS.ROLLOUT every stock hook is one shim that `exec`s `$GOH/gates/hook.sh` (it
DELEGATES), so a hook is stale only when the shim's ABI moves, and is then aging, never refused.

Askers: scripts/hooks_refresh.py and scripts/estate_audit.py (`stale`), lib/ship_item.py
(`refused`), and structural.sh through gates/_hook_currency.sh, which runs this file:

    hook_currency.py --refused|--aging [--goh ROOT] [REPO]   # one `.githooks/<name>` per line

THE reading of a stale stock hook (GOH.HOOKREFRESH, GOH.HOOKCURRENCYDUP): byte-identical to a stock
copy goh once shipped (`retired_hooks.sha256`) or to the one install.sh recorded writing, and not
today's -- exactly the hooks install.sh overwrites without `--force`, so a hook a repo edited is
never called stale; only `.githooks` is read. A stale hook is REFUSED when some `exec` in it runs
no script of the shared `gates/` and its entry is not `same-logic`, else AGING (warned). `stale`
for `scripts/hooks_refresh.py` and `scripts/estate_audit.py`, `refused` for `lib/ship_item.py`, the
`--refused`/`--aging` CLI for `gates/_hook_currency.sh`. Pinned by `test_hooks_refresh.py`.

`retired_hooks.sha256`: digest of every stock hook ever shipped; a repo hook matching one is
pristine to `install.sh`, and `structural.sh` refuses it, naming `install.sh "$PWD"`

`hooks/`: stock `pre-commit` (only delegates, to `gates/commit_gate.sh`), `pre-push` (only
delegates, to `gates/push_gate.sh`), `commit-msg` (`gates/commit_msg.sh`), and `post-rewrite` +
`pre-applypatch` (one body: `gates/sequencer_gate.sh`, the marker check for rebase picks and `git
am` patches, which run no pre-commit). Every file here is installed; `gates/doctor.sh` checks the
same list.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    import contextlib as _contextlib

    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: run it as is
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

GOH_ROOT = Path(__file__).resolve().parent.parent
HOOKS = ".githooks"
_EXEC = re.compile(r"^[ \t]*exec\b.*$", re.MULTILINE)
# `exec bash "$GOH/gates/push_gate.sh"` (0ac0f70) or `exec bash "$commit_gate"` (18330c7 on).
_RUNTIME = re.compile(r'^[ \t]*exec bash "\$(?:GOH/gates/[\w.]+|(\w+))"')


@dataclass(frozen=True)
class Stale:
    name: str
    refused: bool

    @property
    def path(self) -> str:
        return f"{HOOKS}/{self.name}"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _retired(goh: Path) -> dict[tuple[str, str], bool]:
    """(sha256, hook name) -> tagged same-logic, for every stock hook goh ever shipped."""
    out: dict[tuple[str, str], bool] = {}
    try:
        lines = (goh / "retired_hooks.sha256").read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and not line.startswith("#"):
            out[(parts[0], parts[1])] = "same-logic" in line
    return out


def delegates(body: str) -> bool:
    """Every exec in the hook runs a script of the shared checkout's gates/, directly or through a
    variable the hook assigns only such a path (`$root/gates/` is goh hosting itself)."""
    execs = _EXEC.findall(body)
    for line in execs:
        m = _RUNTIME.match(line)
        if not m:
            return False
        if var := m.group(1):
            assigned = re.findall(rf'^[ \t]*{var}="([^"]*)"', body, re.MULTILINE)
            if not assigned or not all(a.startswith(("$GOH/gates/", "$root/gates/"))
                                       for a in assigned):  # fmt: skip
                return False
    return bool(execs)


def hooks_path(repo: Path, env: dict | None = None) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), "config", "core.hooksPath"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    return r.stdout.strip() or HOOKS


def classify(repo: Path, goh: Path = GOH_ROOT, env: dict | None = None) -> list[Stale]:
    """Every stale stock hook in the repo, sorted by name, each with whether it is refused."""
    hp = hooks_path(repo, env)
    if hp != HOOKS and (repo / hp).resolve() != (repo / HOOKS).resolve():
        return []
    hooks, record = repo / HOOKS, repo / HOOKS / ".goh-installed"
    retired = _retired(goh)
    stock_dir = goh / "hooks"
    stock = {p.name: p for p in stock_dir.iterdir() if p.is_file()} if stock_dir.is_dir() else {}
    found = []
    for name in sorted(set(stock) | {n for _, n in retired}):
        dst = hooks / name
        if not dst.is_file():
            continue
        data = dst.read_bytes()
        if name in stock and data == stock[name].read_bytes():
            continue
        cur = _sha(data)
        try:
            recorded = (record / f"{name}.sha256").read_text(encoding="utf-8").strip()
        except OSError:
            recorded = ""
        if cur != recorded and (cur, name) not in retired:
            continue
        same_logic = retired.get((cur, name), False)
        body = data.decode("utf-8", errors="replace")
        found.append(Stale(name, refused=not same_logic and not delegates(body)))
    return found


def stale(repo: Path, goh: Path = GOH_ROOT, env: dict | None = None) -> list[str]:
    """The names of the repo's stale stock hooks, sorted: what `install.sh` would bring current."""
    return [s.name for s in classify(repo, goh, env)]


def refused(repo: Path, goh: Path = GOH_ROOT, env: dict | None = None) -> list[str]:
    """`.githooks/<name>` of each stale hook carrying gate logic of its own."""
    return [s.path for s in classify(repo, goh, env) if s.refused]


def aging(repo: Path, goh: Path = GOH_ROOT, env: dict | None = None) -> list[str]:
    """`.githooks/<name>` of each stale hook that runs today's gate."""
    return [s.path for s in classify(repo, goh, env) if not s.refused]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--refused", action="store_true")
    which.add_argument("--aging", action="store_true")
    ap.add_argument("--goh", type=Path, default=GOH_ROOT)
    ap.add_argument("repo", type=Path, nargs="?", default=Path.cwd())
    a = ap.parse_args(argv)
    top = subprocess.run(["git", "-C", str(a.repo), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=False,
                         stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip
    repo = Path(top) if top else a.repo
    for path in (refused if a.refused else aging)(repo, a.goh):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
