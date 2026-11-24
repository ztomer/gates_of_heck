"""lib/ship_test_rows.py -- `goh test-ships [--delete]`: the ship rows a test left in goh.db.

GOH.FAKESHIPROWS. Before .loop's test_ship_done was made hermetic it ran goh ship against the live
goh.db and left its rows there (item A.0, results under pytest's tmp paths): 54 on 2026-11-24,
which every ship statistic counted. A test's row is one whose result path sits under a system
temp root and not in the ship directory (lib/ship_records.py `test_rows`, the one door to the
`ships` table). Listed by default, a dry run that deletes nothing; `--delete` removes them with
their pin rows in one write, never by hand. Pinned by tests/test_ship_test_rows.py.
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

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ship_records  # lib/, on the path from the line above


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="goh test-ships", description=__doc__.split("\n\n")[0])
    ap.add_argument("--delete", action="store_true", help="delete them (default: list only)")
    a = ap.parse_args(argv)
    rows = ship_records.test_rows()
    for r in rows:
        print(f"test ship row: {r}")
    if a.delete:
        print(f"{ship_records.drop_test_rows()} test ship rows deleted")
    else:
        print(f"{len(rows)} test ship rows (dry run: none deleted; --delete removes them)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
