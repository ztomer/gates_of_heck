# D-0007 — `goh moving-tags` reads a version coarser than `x.y.z` as a moving release line, distro versions included

- **Question:** Is an image tag whose version is coarser than a full `x.y.z` release (`rust:1`,
  `node:22`, `alpine:3`, `redis:7.4`, `postgres:18-alpine`) a moving tag, and is a
  distribution's own version (`ubuntu:24.04`, `debian:13`, `debian:13.1`) an exception?
- **Evidence:** Owner, 2026-11-15, asked "Should version-shaped image tags like rust:1 count as
  moving tags?": "Yes, flag them". Until then `movingtag/reference.rs` passed every digit-bearing
  tag with no channel word, so media_server's `scripts/dev/linux_test.sh` ran `IMAGE=rust:1` past
  the gate (SRV.RUSTTAG, whose done-state expected red). In the fleet's own measurements
  (media_server `image_drift_sweep/tests.rs`) `postgres:18-alpine` is 18.6 one month and 18.7 the
  next; Ubuntu's `24.04` is re-pointed across 24.04.N and the rebuilds between them, Debian's
  `13.1` until 13.2 ships, and both publish dated tags that do name one build
  (`ubuntu:noble-20250925`, `debian:trixie-20250908`). Census with GOH_GRACE=over on 2026-11-15,
  calibrated against a `git grep` of the estate: 9 new sites in 2 repos -- media_server 8 (7 compose
  service images inside servers DECISIONS #9's scope, 1 Dockerfile base), CadGoose 1
  (`FROM ubuntu:26.04`); no other estate file the scanner reads holds one.
- **Options priced:**
  1. One rule by shape: a digit-bearing tag names a build only through a date, a full `x.y.z`
     version (any suffix) or a commit id; anything coarser is a release line. Reads the text alone,
     as the scanner always has; fails `postgres:16.4` and `debian:13.1`, which are each project's
     finest version, so those pin by digest or a dated tag. TAKEN.
  2. Option 1 with distributions exempt (a list: ubuntu, debian, alpine, fedora...): the gate would
     have to know which repositories are distributions and keep the list current, and would still
     float across point releases and rebuilds, which is the property the ruling refuses. Rejected.
  3. Two components (`x.y`) count as a release for everyone: passes `redis:7.4`, which the
     ruling names as moving. Rejected.
  4. A per-project versioning table (postgres and debian are `x.y`, the rest `x.y.z`): a list
     nobody maintains, and `postgres:16.4` is still rebuilt under the same name. Rejected.
  5. Exempt the fleet in goh (by repo path): widens servers DECISIONS #9 from a per-image written
     decision into a blanket pass, which the ruling's own text forbids ("a NEW floating image is a
     decision its author writes down"). Rejected; the fleet keeps its per-image markers.
- **Ruling:** (GOH.MOVINGTAGCOARSE, 2026-11-15) A tag with digits is a moving release line unless a
  date, a full `x.y.z` version or a commit id among its `-`/`_`/`.` parts names one build; a digest
  pins any tag. Distribution versions get no exception: `ubuntu:24.04`, `debian:13` and
  `debian:13.1` are lines, pinned by their dated tag or a digest. The existing pins stand (digest,
  commit id, date, full `x.y.z` with any suffix). The fleet exemption is unchanged: goh carries
  none, and each floating fleet image says `# moving-tag-ok: ... (servers DECISIONS #9)`. Judged
  from `crate::grace::OVER_FROM` (2026-11-23); before it the verdict is the last release's.
- **Falsifier:** `movingtag/tests.rs` -- `rust:1`, `node:22`, `alpine:3` red in a Dockerfile, a
  workflow and a `.sh`; `rust:1.99.1`, `rust:1@sha256:...` and a dated tag green; within the grace
  all of them green; `ubuntu:24.04`, `debian:13`, `redis:7.4`, `postgres:18-alpine` red. In the
  estate: a coarse tag that passes `goh moving-tags` after 2026-11-23 with no marker and no digest.
- **Re-open when:** the owner rules a distribution's version a release; a widely used image
  publishes only coarse tags and no dated or full-version tag, so a digest is the only pin a
  consumer can write (then a reasoned marker or a named exception list is on the table); or the
  registry offers a per-tag immutability flag goh could read.
- **Lives in:** `crates/goh/src/movingtag/reference.rs`, `crates/goh/src/movingtag/scan.rs`,
  `crates/goh/src/movingtag/mod.rs`, `crates/goh/src/movingtag/tests.rs`,
  `crates/goh/src/movingtag/shell_tests.rs`, `docs/map.md`
