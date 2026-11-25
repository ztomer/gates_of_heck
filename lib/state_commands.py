"""lib/state_commands.py -- the `goh state` words beside `retire` and `stray` (lib/state_retire.py
is the command; split at its line cap, GOH.OLDWRITERS).

    goh state contract N [--db DB]   schema file N applied by hand (lib/db_contract.py)
    goh state olds --since X         what still runs goh without commit X (lib/state_olds.py)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def add(sub: argparse._SubParsersAction) -> None:
    c = sub.add_parser("contract", help="apply a by-command schema file, the file copied first")
    c.add_argument("n", type=int)
    c.add_argument("--db", choices=("goh", "goh-cache"), default="goh")
    o = sub.add_parser("olds", help="what still runs goh without a commit")
    o.add_argument("--since", required=True, metavar="X")


def run(a: argparse.Namespace) -> int | None:
    """A's exit code when it is one of these words, else None."""
    if a.cmd == "contract":
        import db_contract

        return db_contract.main(a.n, a.db)
    if a.cmd == "olds":
        import state_olds

        return state_olds.main(a.since)
    return None
