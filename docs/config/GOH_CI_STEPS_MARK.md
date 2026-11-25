# GOH_CI_STEPS_MARK
Area: deps
Kind: setting
Order: 210
Default: env, internal

Set by `push_gate.sh` around the repo's `tools/gate.sh --full`: `local_ci.sh` appends the tree whose
`.gatesrc` `GOH_CI_STEPS` it ran, and a gate that passed without marking this repo gets
`local_ci.sh` run over the worktree after it, with the gap named (homebrew-tap, 2026-10-18). Never
set it in `.gatesrc`.
