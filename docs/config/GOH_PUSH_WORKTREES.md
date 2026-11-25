# GOH_PUSH_WORKTREES
Area: structural
Kind: setting
Order: 60
Default: `~/.cache/goh/push`

Where `push_gate.sh` checks the pushed commit out. Never the temp directory: SwiftLint's baseline
matches nothing under `/private/tmp` or `$TMPDIR` and everything under `/Users` (measured
2026-09-21).
