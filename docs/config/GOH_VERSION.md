# GOH_VERSION
Area: runtime
Kind: setting
Order: 540
Default: the release tag (`lib/goh_version.sh`)

The version `goh --version` reports, baked in at build time (`crates/goh/build.rs`;
`scripts/build-goh.sh` passes it to a build from an export, which has no .git): `X.Y.Z` on a commit
tagged vX.Y.Z, `X.Y.Z-N-g<sha>` past one, `unknown` with no release tag. Never Cargo.toml's
placeholder (GOH.SHIPRELREUSE). Not user config.
