# GOH_EXPORT_KEEP
Area: structural
Kind: setting
Order: 50
Default: unset

Ignored files the pre-push export must carry (space-separated, repo-relative), e.g. a compile-flag
marker the manifest reads. The gate runs on a clean worktree of the pushed commit
(`gates/push_gate.sh`); tracked files are always there, ignored ones only if named here. The proven
cache keys every step on these files' content, so a green step that REWRITES one moves its own key
and is never recorded: the runner names repo, step and path, and is red unless the tracked top-level
`kept_rewrite_allow.json` carries that step and path with a reason (`lib/kept_rewrite.py`; a stale
entry is red too).
