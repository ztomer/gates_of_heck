# GOH_SHARE_STEPS
Area: pushes
Kind: internal
Order: 660
Default: set by `local_ci.sh`

`:`-joined files, each one `local_ci.sh` scheduler's count of steps running NOW (`GOH_CI_JOBS` > 1);
each divides the live share, so a step grows as its siblings finish. Not a setting.
