# GOH_SWIFT
Area: runtime
Kind: setting
Order: 570
Default: unset (`xcrun --find swift`, then PATH)

Explicit swift driver for `swift_gate.sh` and any script sourcing `gates/swift_toolchain.sh`.
Refused when not executable. Exists because a swiftly toolchain on PATH shadowed Xcode's and the
gate failed for three days as "environmental".
