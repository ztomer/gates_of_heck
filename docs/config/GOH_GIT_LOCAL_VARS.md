# GOH_GIT_LOCAL_VARS
Area: runtime
Kind: internal
Order: 260
Default: set by `gates/_git_env.sh`

git's repository-binding variables (`git rev-parse --local-env-vars`), asked once per process tree
and dropped by every git call on a foreign repository (contract #12). Taken from the environment
only when it names `GIT_DIR`; otherwise asked again. Not a setting.
