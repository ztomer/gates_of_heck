# GOH_TMP_LEAKS
Area: local-ci
Kind: setting
Order: 140
Default: **warn**

Every `local_ci.sh` step runs with a TMPDIR of its own, the lowest free
`$TMPDIR/goh.noindex/step.NN` (EST.TMPLEAK, `lib/step_tmp.sh`). What a GREEN step leaves there is
named (`left N temp entries (removed): ...`) and removed; a red step's is kept with its log. `red`
fails a leaking step (`(temp leak)`, never recorded as proven); `off` runs steps in the user's
TMPDIR. The step's PATH leads with an `sccache` that runs under the user's TMPDIR, so a server a
step starts never lives in a slot about to go (GOH.SCCACHETMP). 2026-11-14: the user's TMPDIR held
34,022 entries no passing suite was ever asked about. Read from `.gatesrc` only.
