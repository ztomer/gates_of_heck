# GOH_RUST_LINT_CONFIGS
Area: rust
Kind: setting
Order: 100
Default: unset

Extra clippy configurations to lint, `:`-separated, each a string of cargo argv (e.g. `--target
x86_64-unknown-linux-musl -p agent`). The gate's own clippy step covers ONE cfg -- this machine's
target with all features on -- and a crate that is part `cfg(target_os = ...)` or part `cfg(feature
= ...)` has halves that command never compiles and therefore cannot report on. Each entry runs
`cargo clippy --all-targets <argv> -- -D warnings`. A `--target` whose std is not installed is a
hard failure naming the `rustup target add`, never a skip: a step that inspects nothing must not
read as a pass. Unset leaves the single-cfg behaviour with a printed nudge.
