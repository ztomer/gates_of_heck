# GOH_HEAVY_ADMIT_WAIT
Area: runtime
Kind: setting
Order: 60
Default: `300`

Seconds a session's heavy Bash command (a cargo/swift/xcodebuild/gradle/go build, a shellcheck
sweep) waits for a gate slot before it runs anyway, naming the holders (lib/heavy_admit.py,
GOH.ADMISSION). Read by the Bash guard when it rewrites the command.
