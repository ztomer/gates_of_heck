# GOH_BUILD_GATE_ONLY, GOH_BUILD_PUBLISH_CHECK_ONLY
Area: runtime
Kind: setting
Order: 520
Default: unset

`scripts/build-goh.sh` stops after its platform gate, or after its publish check, before cargo: the
tests' way to drive one half without a build. Not user config.
