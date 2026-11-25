# GOH_STAGED_GUARD_RUN
Area: deps
Kind: setting
Order: 180
Default: env, internal

Set by `gates/staged_guards.sh` on the pytest run of a commit's declared source guards (`#
goh-reads-staged:`): goh's suite (`tests/_drift_guard.py` `prebuild_live`) then builds no goh
binary, which a guard reading the tree's text never spawns and a fixture commit has no cargo to
build. Never set it in `.gatesrc`.
