# GOH_RELEASE_VERSION
Area: runtime
Kind: setting
Order: 560
Default: set by `tools/release-kit/release.sh`

The version being cut, exported into the `--verify CMD` run (and the archive build) so the command
can assert the INSTALLED binary answers with exactly it. Read it, never set it by hand.
