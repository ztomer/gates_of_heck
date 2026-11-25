# GOH_SWIFT_COV_FLOORS
Area: swift
Kind: setting
Order: 60
Default: unset

Path to a per-target floors JSON (same schema as `coverage_gate.sh --floors-json`), checked BEFORE
the package floor. Keys are path prefixes relative to `Sources/`, matched segment-wise, so
`App/Core` floors a directory without splitting the package into targets. A key matching no measured
source is exit 2, never a silent pass. Pointing at a missing file is a named refusal.
