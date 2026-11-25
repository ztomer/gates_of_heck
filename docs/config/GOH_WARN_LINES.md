# GOH_WARN_LINES
Area: runtime
Kind: setting
Order: 120
Default: `20`

Most `⚠` and `↔` status lines a PASSING step prints after its ok line; the rest is counted
(GOH.STEPWARN). A `↔` line is a measurement against its bound (tui `measure`: every coverage gate's
number, GOH.COVSHOW), shown by `goh_step` and by `local_ci.sh` after a passing step.
