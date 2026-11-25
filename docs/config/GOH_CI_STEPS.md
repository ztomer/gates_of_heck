# GOH_CI_STEPS
Area: local-ci
Kind: setting
Order: 10
Default: unset (required unless `--step` given)

Colon-separated shell-command list. `.gatesrc` steps run first, then `--step` ones. Keep colons OUT
of step strings — put `${VAR:+flag}` logic in a repo script and invoke that.
