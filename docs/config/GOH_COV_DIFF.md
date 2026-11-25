# GOH_COV_DIFF
Area: coverage
Kind: setting
Order: 110
Default: `on`

rust only (GOH.DIFFCOV, `gates/diff_cov.py`): every executable Rust line the change adds or edits
must be covered by the same run's report (target 100%), named `file:line` when not; judged on the
new side of `git diff -M` from the change's base (`GOH_CHANGE_BASE` for a push, else the merge base
with the upstream, then with `main`, then HEAD's parent). `warn` names them without failing (a
consumer's roll-out), `off` skips it, saying so. A separate reader of the final lcov parts: it never
makes or merges them. Swift and C coverage are out of its scope.
