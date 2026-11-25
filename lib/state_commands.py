"""lib/state_commands.py -- the `goh state` words beside `retire` and `stray` (lib/state_retire.py
is the command; split at its line cap, GOH.OLDWRITERS).

    goh state contract N [--db DB]   schema file N applied by hand (lib/db_contract.py)
    goh state olds --since X         what still runs goh without commit X (lib/state_olds.py)
    goh state check [--portable]     the rows whose columns do not give their document back
    goh state backfill               the documents from before the columns, put into them
    goh state strip                  the documents the columns give back, dropped (in the cutover)
    goh state cutover [--abort]      every document out, every path a token, once
                                     (lib/state_cutover.py); --abort: a dead one's fence down
    goh state rekey [--undo]         the paths alone (lib/state_rekey.py); --undo: absolute again
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
    k = sub.add_parser("check", help="the rows whose columns do not give their document back")
    k.add_argument("--portable", action="store_true", help="and the paths that name this host")
    sub.add_parser("backfill", help="documents from before the columns, put into them")
    sub.add_parser("strip", help="documents the columns give back, dropped (in the cutover)")
    u = sub.add_parser("cutover", help="every document out of goh.db, every path a token; once")
    u.add_argument("--abort", action="store_true", help="lift the fence of a cutover that died")
    r = sub.add_parser("rekey", help="every stored path a token, every repo its key")
    r.add_argument("--undo", action="store_true", help="absolute again")


def run(a: argparse.Namespace) -> int | None:
    """A's exit code when it is one of these words, else None."""
    if a.cmd == "contract":
        import db_contract

        return db_contract.main(a.n, a.db)
    if a.cmd == "olds":
        import state_olds

        return state_olds.main(a.since)
    if a.cmd == "check":
        import state_columns

        return state_columns.main(a.portable)
    if a.cmd == "backfill":
        import state_backfill

        return state_backfill.main()
    if a.cmd == "strip":
        import state_strip

        return state_strip.main()
    if a.cmd == "cutover":
        import state_cutover

        return state_cutover.main(a.abort)
    if a.cmd == "rekey":
        import state_rekey

        return state_rekey.main(a.undo)
    return None
