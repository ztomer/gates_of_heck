#!/usr/bin/env python3
"""Print `<lines>\t<path>` for every path named in a ratchet baseline.

The current-values side of the length ratchet (check_baseline_ratchet.py
--current-from-command): the baseline lists ceilings for cap-exempt files;
this measures those same files today so the ratchet can compare. A listed
path that is not a file prints 0: a vanished file is under any ceiling
(check_exclusion_has_ceiling.py is the check that notices a stale entry), and
a sentinel row such as `0 __under_cap_sentinel__` -- the shape a repo with no
real exemptions keeps so the pairing check still runs -- measures 0 against
its 0, which is how an empty ratchet passes instead of aborting on no input.

    python3 checks/loc_of_baseline_files.py .gates_loc_baseline.txt
"""
import os
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: loc_of_baseline_files.py <baseline>", file=sys.stderr)
        return 2
    with open(sys.argv[1], encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            raw = line.split("\t", 1) if "\t" in line else line.split(None, 1)
            if len(raw) != 2:
                continue
            path = raw[1].strip()
            count = 0
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    count = sum(1 for _ in f)
            print(f"{count}\t{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
