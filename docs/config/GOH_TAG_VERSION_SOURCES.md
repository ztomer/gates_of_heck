# GOH_TAG_VERSION_SOURCES
Area: structural
Kind: setting
Order: 80
Default: `file:VERSION cargo:Cargo.toml`

Version sources `push_gate.sh` checks a pushed `refs/tags/v<semver>` against — space-separated
`kind:path`. Kinds: `file` (first meaningful line), `cargo` (`[workspace.package]`/`[package]`
`version`), `swift` (any string constant whose value is `x.y.z` — a SwiftPM package that declares
its version in source, where the constant is usually named `marketing` or `version` and is usually
accompanied by a build number that is NOT a release number; `swift:` returns EVERY release-shaped
constant, so a file declaring two different ones still fails rather than one being picked). `plist`
(`<key>`/`<string>` pairs in an Apple bundle `Info.plist`; without it a bundle's version is
unreadable, because `file:` reads the XML **declaration** `<?xml version="1.0"…?>` as the version
and reports a correct release as a mismatch — measured on ZeroThunder's `v2.10.0`, which no other
kind could check) `pyproject` (the `[project] version` a Python distribution declares; without it a
packaging-only repo has no readable source and `file:` reads whatever the first meaningful line
happens to be), `xcodegen` (the `MARKETING_VERSION:` key in an XcodeGen `project.yml`, and only that
key: the same file pins package and Swift versions in the same shape) and `gradle` (`versionName` in
a `build.gradle(.kts)`, Kotlin or Groovy spelling, and only that name: dependency coordinates carry
versions too; measured on koffee_big's `v2.11.5`, whose release was declared in both files and
readable by neither) and `changelog` (the newest `## vX.Y.Z` heading of a CHANGELOG.md: a repo whose
version IS its tag, as goh's is since GOH.SHIPRELREUSE, declares the release only in the stanza its
release commit cut). A `path` may be a glob, expanded against the TREE AT THE COMMIT; scope it
(`cargo:crates/*/Cargo.toml`) rather than `**`, which sweeps `vendor/` and invents findings about
crates this repo does not ship. Empty or all-unknown is exit 2, never a pass over nothing. `goh
tag-version --root X` and `goh lock-version --root X` read it from the environment, else from X's
own `.gatesrc` (`gatesrc::repo_setting`), so a caller that sourced nothing gets the repo's answer.
