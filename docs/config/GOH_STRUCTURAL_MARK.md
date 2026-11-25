# GOH_STRUCTURAL_MARK
Area: deps
Kind: setting
Order: 160
Default: env, internal

Set by `gates/commit_gate.sh` and `push_gate.sh` around the repo's `tools/gate.sh`: `structural.sh`
appends its scope and tree to the file it names, and a gate that passed without marking this repo's
scope gets structural run after it, with the gap named (BACKLOG 6.7). Never set it in `.gatesrc`.
