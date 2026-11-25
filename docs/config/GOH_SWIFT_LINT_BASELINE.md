# GOH_SWIFT_LINT_BASELINE
Area: swift
Kind: setting
Order: 110
Default: unset (bare `swiftlint --strict`)

Path to baseline JSON. When set, lint becomes a shrink-only ratchet: baselined violations tolerated,
new ones fail named, shrunk or vanished ones fail with the re-record command (a named warning before
`crate::grace`'s day).
