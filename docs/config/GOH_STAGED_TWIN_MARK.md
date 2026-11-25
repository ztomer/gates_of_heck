# GOH_STAGED_TWIN_MARK
Area: deps
Kind: setting
Order: 170
Default: env, internal

Set by `gates/commit_gate.sh` around the repo's `tools/gate.sh --staged`: `py_staged.sh` and
`swift_staged.sh` append their name and tree to the file it names, and every repo whose index holds
a `.py` / `.swift` that no gate marked gets that twin run after it (GOH.STAGEDPARITY; which push
checks need a twin is `tests/test_staged_parity.py`). Never set it in `.gatesrc`.
