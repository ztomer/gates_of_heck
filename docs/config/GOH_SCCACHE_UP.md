# GOH_SCCACHE_UP
Area: rust
Kind: setting
Order: 10
Default: on

`off`: `lib/goh-rustc` starts no missing sccache server and the cache sweep asks none
(GOH.SCCACHEROOTFOLLOW). The suite sets it (`tests/suite_home.py`), so no test reaches the machine's
server.
