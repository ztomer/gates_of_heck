# Commit messages (`gates/commit_msg.sh` via `hooks/commit-msg`, and `gates/push_gate.sh`)
Order: 30
Level: 2

| Key | Default | Meaning |
|---|---|---|
<!-- rows to 20 -->
| (file) `tools/commit_msg.sh` | absent (nothing runs) | The repo's own commit-message layer, as `tools/gate.sh` is its tree layer: when the file exists the stock `commit-msg` hook (`gates/commit_msg.sh`) runs `bash tools/commit_msg.sh <message-file>` after the opt-in rules, and a non-zero exit refuses the commit. For a rule only one repo carries (a private consumer reads each message for a real figure or name through a private consumer's detectors), so no repo hand-edits its installed hook, which a reinstall overwrites and `doctor` reports as drift. The pushed range is the repo's to re-read in its `tools/gate.sh --full`. |
<!-- rows -->
