# GOH_BINREACH
Area: local-ci
Kind: setting
Order: 200
Default: `on`

`off` is the kill switch of the binary reach (`lib/bin_reach.py`, GOH.BINREACH): the enqueue's
pre-step then chooses every binary test of a subcommand a Rust change reaches, as the gate does,
instead of only those whose stored profile reaches the change.
