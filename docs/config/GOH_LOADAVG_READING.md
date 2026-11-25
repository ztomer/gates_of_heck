# GOH_LOADAVG_READING
Area: claude-hooks
Kind: internal
Order: 130
Default: unset

THE SEAM: `"1m 5m 15m"` read in place of `os.getloadavg()` (`tests/test_capacity.py`); anything but
three numbers is an unread part. Not a setting.
