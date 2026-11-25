# GOH_NO_KILL_BY_NAME
Area: structural
Kind: setting
Order: 230
Default: unset (check skipped)

Set to `1` to run `goh kill-by-name`: no `pkill` or `killall` by name, and no `pgrep`/`pidof`
feeding a `kill`, in tracked code and scripts (a `pkill -P`/`-g`/`-s` is owner-scoped and passes). A
name matches processes the caller does not own. Survivors go in `kill_by_name_allow.json` at the
repo root: one call in one file each (in Python the whole statement, whitespace and trailing commas
ignored, so a reformat keeps it; an entry holding only part of the call still matches and is named
for re-keying), with a `reason` and a `status` (`legitimate` or `unreviewed`); a stale entry fails.
Opt-in per repo, seeded first. Same `GOH_EXCLUDE`.
