# GOH_BIN
Area: structural
Kind: setting
Order: 10
Default: unset

Explicit path to the native `goh` binary. Set-but-missing is reported once and the gate REFUSES
(there is no other tier since Phase N3); unset resolves the build of the commit the gate runs from
(`bin/by-stamp/<sha1 of its source stamp>/goh`: a current `bin/goh` is adopted there, a commit the
checkout has moved past is built there with `build-goh.sh --rev`), then `goh` on `PATH`.
