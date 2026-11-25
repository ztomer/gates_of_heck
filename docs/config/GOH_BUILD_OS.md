# GOH_BUILD_OS, GOH_BUILD_ARCH
Area: runtime
Kind: setting
Order: 470
Default: the host (`uname -s`/`-m`; the binary's `cfg!`)

The platform seam of `scripts/build-goh.sh` and `crates/goh/src/platform.rs`: an unsupported OS/arch
is a hard refusal, and these make both the refusal and the acceptance testable on one machine
(`tests/test_goh_platform_gate.py`, `crates/goh/tests/cli.rs`). Not user config.
