# GOH_RUSTC_OS
Area: rust
Kind: setting
Order: 90
Default: `uname -s`

The system `lib/goh-rustc` acts for. On `Darwin` a proc-macro's link gets
`-Wl,-install_name,@rpath/lib<crate>.dylib`, so its dylib, and the keys of the crates using it, no
longer carry the build dir's path (GOH.SCCACHEPATHS, F2). Elsewhere the argv passes unchanged (an
ELF proc-macro's path content is unverified). A SEAM: a test names either system.
