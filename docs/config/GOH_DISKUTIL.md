# GOH_DISKUTIL
Area: deps
Kind: setting
Order: 190
Default: env, internal

The `diskutil` program `lib/encrypted_unmount.py` (the discard guard's FileVault-unmount rule,
LOOP.MISTAKEGUARDS) reads volumes with (`info -plist`, `apfs list -plist`); unset, the real one. A
test seam: the suite points it at a fake answering from a fixture, so no test reads the real disks.
Never set it in `.gatesrc`.
