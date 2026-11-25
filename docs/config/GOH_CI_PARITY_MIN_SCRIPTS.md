# GOH_CI_PARITY_MIN_SCRIPTS
Area: local-ci
Kind: setting
Order: 80
Default: **1**

The parity check's floor: a scan that finds fewer repo scripts in CI's `run:` steps than this has
lost its subject (red), since a scan of nothing reads as clean. Set at about nine tenths of what it
reads; lower it with the tree. Not a whole number is exit 2.
