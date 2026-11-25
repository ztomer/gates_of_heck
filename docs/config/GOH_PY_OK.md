# GOH_PY_OK
Area: python
Kind: internal
Order: 40
Default: set by the resolver

Not a setting: the resolver's verdict cache, equal to `GOH_PY` when that interpreter was already
judged in this process tree, so a nested gate does not resolve again. A test that plants a fake
through `GOH_PY` sets both to skip the floor check on purpose.
