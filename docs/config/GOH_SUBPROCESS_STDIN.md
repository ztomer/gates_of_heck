# GOH_SUBPROCESS_STDIN
Area: structural
Kind: setting
Order: 270
Default: on

Unset or `on` runs `goh subprocess-stdin` in the structural pipeline at both scopes; only a written
`off` skips it, so an opt-out is a decision in the repo's own file, the way `stdin=None` is at a
call. **On by default since 2026-10-13** (BACKLOG 3.4), ahead of the estate sweep, by the owner's
ruling that goh ships a check and each consumer clears its own debt in its own session: a repo with
undeclared calls reads red at its next commit (996 findings in 17 of 34 repos on 2026-10-09; 15 rows
remain, BACKLOG Downstream). Any other value is exit 1 NAMING THIS KEY, never a silent off. `goh
subprocess-stdin` run by hand ignores it. A repo clears the red by declaring each call, or with `goh
subprocess-stdin --seed` (one commit of its own, over committed Python): today's findings become
`unreviewed` entries in `subprocess_stdin_allow.json`, or `subprocess_stdin_allow.d/seed-NN.json`
past 400; a reseed keeps reviewed entries as written and drops stale ones.
