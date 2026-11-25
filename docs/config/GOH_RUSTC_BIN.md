# GOH_RUSTC_BIN
Area: rust
Kind: setting
Order: 80
Default: `~/.local/bin/goh-rustc`

Where `lib/rustc_wrap.py apply` writes the trampoline that `RUSTC_WRAPPER` in `~/.zshenv`
(`$ZDOTDIR/.zshenv`) and `build.rustc-wrapper` in `~/.cargo/config.toml` (`$CARGO_HOME`) both name
(GOH.RUSTCWRAPWIRE): it runs the main checkout's `lib/goh-rustc`, or plain sccache when that is
gone, and the zshenv line names sccache when the trampoline itself is gone. `check` exits 1 naming
each disagreement.
