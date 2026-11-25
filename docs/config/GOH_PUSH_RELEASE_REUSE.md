# GOH_PUSH_RELEASE_REUSE
Area: pushes
Kind: setting
Order: 380
Default: unset (rule off; goh: `on`)

`on`: `gates/push_gate.sh` gates a pushed commit with the release land's reduced set
(`lib/release_gate.py run`: `gates/structural.sh --full` and the tests that read CHANGELOG.md or
`changelog/`) instead of the full gate when ALL hold: it has one parent, `tools/land.sh` recorded
that parent in `goh-landed` (only a green land is recorded), and the diff parent..commit changes
release files only (`release_gate.py`'s one definition, shared with the land). Anything else is the
full gate, and the push names why (`pre-push: the full gate: <reason>`). Read by name in a child,
like `GOH_LANDED_ONLY`. For a repo whose tests are pytest files under `tests/` (the probe), so goh's
alone today (GOH.SHIPRELREUSE). Pinned by `test_push_release_reuse.py`.
