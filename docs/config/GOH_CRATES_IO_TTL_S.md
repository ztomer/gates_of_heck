# GOH_CRATES_IO_TTL_S
Area: deps
Kind: setting
Order: 230
Default: `21600` (6 h)

How old a cached answer may be. Under `GOH_DEPS_STRICT` / `GOH_DEPS_RATCHET`, a release newer than
this can be missed for up to the TTL -- the stated price of not asking crates.io once per dependency
per crate per push.
