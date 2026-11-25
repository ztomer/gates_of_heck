# GOH_SHELL_LINT_SEVERITY
Area: structural
Kind: setting
Order: 360
Default: `warning`

The lowest shellcheck severity `goh shell-lint` judges: `error`, `warning`, `info` or `style`; any
other value is exit 2 NAMING THIS KEY, never a silent fallback. Until 2026-11-02 the lint ran
`shellcheck --severity=error` and nothing below it reached any repo's gate: an unused variable
(SC2034), a `cd` with no `|| exit` (SC2164), an unquoted `$(...)` (SC2046) were all invisible
(GOH.SHSEV). An ERROR still fails outright and is never seedable. Everything between `error` and
this key is DEBT held by `shell_lint_seed.json` at the repo root, a per-`file#SCnnnn` count that may
only fall (`crate::seedcount`'s four directions, shared with `goh fixed-sleep`): a finding over its
count fails with its sites; a finding under it fails until the seed is lowered; an entry for a file
or code gone clean, or for a file no longer in scope, is STALE and fails; a corrupt seed is an
error. The code is in the key so a fixed SC2034 leaves no room for a new SC2164 in the same script.
At `--staged` only the entries for the files the commit brings are judged. `goh shell-lint --seed`
writes today's findings (refused while any error stands, or under the `GOH_SHELL_LINT_MIN_FILES`
floor). **Why `warning` and not `info`** (measured 2026-11-02 over every gated repo's tracked
shell): 23 repos held 498 findings at `info` and above: 86 warnings, 1 error and 411 infos -- 114
SC2016 (a deliberate literal in single quotes), 76 SC2086, 67 SC2015 (`A && B || C`, the house's own
idiom), and 107 SC1091 "not following", which depends on the directory and `-x` shellcheck runs from
and so reads differently from the staged temp checkout than from the worktree: a stale-entry-fails
ratchet over a finding that flaps with the working directory is a gate that lies. A repo that wants
infos writes `GOH_SHELL_LINT_SEVERITY=info` and seeds them. `GOH_SHELL_LINT_SEVERITY=error` is the
written opt-out for a repo that has not yet cleared or seeded its warnings (the way
`GOH_SUBPROCESS_STDIN=off` is). The verdict cache is keyed on the severity.
