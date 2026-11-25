# GOH_LAUNCH_APP_OPEN
Area: deps
Kind: setting
Order: 100
Default: `/usr/bin/open`

env, `goh launch-app` only (`crates/goh/src/launchapp.rs`, GOH.CLEANOPEN): the opener it runs with
an EMPTY environment, read before the clear. A test seam: the real launch is `/usr/bin/open`, the
one launch that gives the app launchd's login environment rather than the caller's.
