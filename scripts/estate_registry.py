#!/usr/bin/env python3
"""estate_registry.py -- the estate's registry: what `install.sh` recorded, and the one-time seed.

`install.sh` records every repo it installs into (BACKLOG 6.14), one line per toplevel, beside
the estate audit's state file. This is the reader and the ONE-TIME SEED: a repo installed before
the registry existed is in the estate already, and a seed that never runs leaves the first sweep
reading a smaller estate than the machine has.

    python3 scripts/estate_registry.py list                # what is registered, and what is gone
    python3 scripts/estate_registry.py seed                # once: the ~/Projects walk plus ~/.claude/skills
    python3 scripts/estate_registry.py record /path/to/repo

`--root` names the directory to walk (the estate root, `GOH_ESTATE_ROOT`), `--extra` names a
repo to add beside it (`~/.claude/skills` is the one that lives outside the estate root), and
`--state` names the audit's state file, whose directory holds the registry.

The seed is ADDITIVE and idempotent: running it twice registers the same estate twice over, and
never removes a line -- a repo that moved is a gone path and a new one, and the owner is told
both.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import estate_registry  # noqa: E402

sys.path.insert(0, str(ROOT))
from tui.lib import info, ok, warn  # noqa: E402

DEFAULT_EXTRA = Path.home() / ".claude" / "skills"


def _seed(root: Path, extras: list[Path], registry: Path) -> list[Path]:
    """(what was added). Everything under the root, and every extra that is a repository."""
    candidates = list(estate_registry.walk_repos(root))
    for extra in extras:
        if extra.is_dir() and (extra / ".git").exists():
            candidates.append(extra)
    return [repo for repo in candidates if estate_registry.record(repo, registry)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="what is registered")
    p_list.add_argument("--state", type=Path, default=estate_registry.state_file())

    p_seed = sub.add_parser("seed", help="register the estate root's walk and the extras")
    p_seed.add_argument("--root", type=Path, default=None)
    p_seed.add_argument("--extra", type=Path, action="append", default=None)
    p_seed.add_argument("--state", type=Path, default=estate_registry.state_file())

    p_record = sub.add_parser("record", help="record one repo (what install.sh does)")
    p_record.add_argument("path", type=Path)
    p_record.add_argument("--state", type=Path, default=estate_registry.state_file())

    args = ap.parse_args(argv)
    registry = estate_registry.registry_path(args.state)

    if args.command == "list":
        repos, bad = estate_registry.read(registry)
        for r in repos:
            info(f"{r}{'' if r.exists() else '  (gone)'}")
        for line in bad:
            warn(f"registry line is not an absolute path: {line}")
        info(f"{len(repos)} registered in {registry}")
        return 0

    if args.command == "seed":
        root = args.root or Path(os.environ.get("GOH_ESTATE_ROOT", Path.home() / "Projects"))
        extras = args.extra if args.extra is not None else [DEFAULT_EXTRA]
        added = _seed(root, [Path(e) for e in extras], registry)
        for r in added:
            ok(f"registered {r}")
        info(f"{len(added)} added to {registry} (seeded from {root})")
        return 0

    if args.command == "record":
        target = Path(args.path).expanduser().resolve()
        if estate_registry.record(target, registry):
            ok(f"registered {target}")
        else:
            info(f"already registered: {target}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
