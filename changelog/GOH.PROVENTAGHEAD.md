* goh's own suite is green on a release commit again (GOH.PROVENTAGHEAD). CI on v0.126.0 failed 17
  binary-identity tests: the tests carried a trees-only copy of the source stamp, so on a HEAD
  carrying a release tag (stamp `<trees> vX.Y.Z`) every binary they faked read as "not built from
  HEAD's source", and structural.sh's currency check read any release build as BEHIND a gates copy
  with no tag reachable (`unknown`). The stamp now has ONE formula, `goh_stamp_at` in
  `lib/goh_version.sh` (`bash lib/goh_version.sh --stamp ROOT [REV]`), asked by the resolver,
  `scripts/build-goh.sh` and the tests alike; the currency check derives its want one way, tagged
  or not, and compares a release only with a release (with none reachable there is nothing to be
  behind). The tests set HEAD's tag state themselves and run both a tagged and an untagged HEAD.
