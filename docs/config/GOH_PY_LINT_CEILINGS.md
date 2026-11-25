# GOH_PY_LINT_CEILINGS
Area: python
Kind: setting
Order: 140
Default: unset (every staged finding fails)

A JSON file of `{"path.py": count}`: the ruff findings each file may carry at commit
(`gates/py_lint_ceiling.py`, BACKLOG 4.4). At commit a file HEAD carries is judged by its DELTA
(GOH.LINTDELTA): a finding the change adds (rule, message and line text, against HEAD's own
findings) is red whatever the ceiling, and findings that predate the change are one counted warning,
not a failure; the push judges every file whole. A staged file over its ceiling fails with its
findings, under it fails with the number to write, and an unlisted file carries none; a ceiling
raised above HEAD's, or the file missing from the commit, is a finding. Seed it with `python3
$GOH_DIR/gates/py_lint_ceiling.py --seed <path>`. ruff runs the repo's rules PLUS the house set
(`gates/ruff_house.py`, RUFF.RULES) under it, at commit and, through `py_gate.sh` or `py_staged.sh
--all`, at push; a ceiling for a file the commit does not carry is stale and fails. Unset, a repo is
judged on the PLW1510 floor and warned with the house set's count. `ruff format --check` stays
strict.
