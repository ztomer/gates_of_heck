# GOH_PYTEST_KEEP_BYTES
Area: python
Kind: setting
Order: 90
Default: `1073741824` (1 GiB)

The size bound on those kept roots, newest first: the first that would take the kept total past it
goes, with every older one. Eleven kept roots of ~5 GiB held 55.3 GiB of TMPDIR (GOH.PYTESTTMP).
