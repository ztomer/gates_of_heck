# GOH_PYTEST_KEEP
Area: python
Kind: setting
Order: 80
Default: `3`

How many older kept `goh-pytest.*` temp roots (a failed run's) survive when a run claims its own
(`gates/_pytest_root.sh`, every pytest goh starts). A live root, one whose `.goh-owner` pid is alive
or whose tree moved within the hour, is never cut and counts toward this.
