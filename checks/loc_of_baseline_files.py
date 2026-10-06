#!/usr/bin/env python3
"""Print `<lines>\t<path>` for every path named in a ratchet baseline.

The current-values side of the length ratchet (check_baseline_ratchet.py
--current-from-command): the baseline lists ceilings for cap-exempt files;
this measures those same files today so the ratchet can compare. Keys come
from the shared reader, so JSON baselines measure their keys — reading the
JSON as line format measured a phantom file named after the value fragment
and failed every JSON baseline as growth (caught 2026-09-23 by the native
ceiling port's parity test). A listed path that is not a file prints 0: a
vanished file is under any ceiling (check_exclusion_has_ceiling.py is the
check that notices a stale entry), and a sentinel row such as
`0 __under_cap_sentinel__` -- the shape a repo with no real exemptions
keeps so the pairing check still runs -- measures 0 against its 0, which
is how an empty ratchet passes instead of aborting on no input.

    python3 checks/loc_of_baseline_files.py .gates_loc_baseline.txt
"""

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_exclusion_has_ceiling import baseline_keys_ordered  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: loc_of_baseline_files.py <baseline>", file=sys.stderr)
        return 2
    for path in baseline_keys_ordered(Path(sys.argv[1])):
        count = 0
        if os.path.isfile(path):
            with open(path, "rb") as f:
                count = sum(1 for _ in f)
        print(f"{count}\t{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
