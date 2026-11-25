# GOH_SWIFT_WARN_DIRS
Area: swift
Kind: setting
Order: 50
Default: top-level dirs of git-tracked `*.swift`

Xcode mode: comma-separated first-party dirs whose compiler warnings fail the gate
(`checks/check_swift_warnings.py` over the `xcodebuild test` log). SPM mode builds with
`-warnings-as-errors` instead.
