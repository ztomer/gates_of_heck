# GOH_LAND_PRE
Area: pushes
Kind: setting
Order: 30
Default: none

The pre-land step `tools/land.sh` runs BEFORE the land lock and the gate's slot, admitted by a gate
slot of its own and bounded at `GOH_LAND_PRE_MAX` (default 10m): the environment, else the repo's
`.gatesrc`; run in the worktree with main's tip as `$1`. A red one lands nothing; one that reaches
its deadline is no verdict, and the gate decides (GOH.PRESTEPDEADLINE). goh declares
`tools/affected_tests.py --run`, the tests the diff names or imports plus every tree reader
(GOH.TREETESTSFIRST), and, for a change to the binary's build inputs (`crates/`, `Cargo.toml`,
`Cargo.lock`, `rust-toolchain.toml`, and each file outside them a crate embeds with
`include_str!`/`include_bytes!`/`include!`, derived by `gate_identity.build_inputs`,
GOH.BUILDINCLUDES), every test that runs the goh binary (GOH.AFFECTEDBINARY).
