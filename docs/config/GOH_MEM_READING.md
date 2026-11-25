# GOH_MEM_READING
Area: pushes
Kind: internal
Order: 550
Default: unset

THE SEAM: a file of `key=value` words (`avail_pct=5 swap_used_mb=40000 mem_total_mb=65536`) read at
every poll in place of the OS, so a test can fake a low reading and lift it
(`tests/test_mem_admit.py`). Not a setting.
