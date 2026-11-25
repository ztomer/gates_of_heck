# GOH_RUFF_CONFIG_PARITY
Area: python
Kind: setting
Order: 150
Default: `warn`

`on` or `warn`, with `GOH_PY_LINT_CEILINGS`: the repo's OWN ruff config must select every house rule
(`gates/ruff_house.py`), so the `ruff check` typed by hand judges what the gate judges
(GOH.LINTPARITY: E501 passed by hand and failed at commit). Read from ruff's resolved settings
(`--show-settings`, with and without the house set), so any config shape counts. `on` makes a gap a
finding naming the config and the `extend-select` line to add; `warn` names it and passes until
2026-12-07 (`gates/ruff_house.py` `OVER_DAY`, GOH.RUFFHOUSESET), from which unset and `warn` are
`on`, and a repo with no `GOH_PY_LINT_CEILINGS` whose files the house set flags is refused, not
warned. A vended base config (`extend =`) is not by construction: a repo's own `select` replaces its
`extend-select`, and an unset `${GOH_DIR}` fails ruff (probed 2026-11-23).
