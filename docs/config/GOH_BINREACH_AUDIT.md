# GOH_BINREACH_AUDIT
Area: local-ci
Kind: setting
Order: 210
Default: (one tree in ten)

`always` or `never` forces the binary reach's audit (`lib/bin_reach_audit.py`): after the verdict,
the pre-step also runs the binary tests the reach left out and counts each red one as a disagreement
(a fault item, `goh land-profile`).
