# GOH_GATE_SLOT
Area: pushes
Kind: internal
Order: 580
Default: set by `push_gate.sh`

`"<slot file> <owner pid>"`, exported by a gate that holds a slot, so a gate nested inside it (a
metarepo gate running a child's push gate, `land.sh` -> push gate) takes none rather than waiting on
its own parent's slot. Stripped by a hermetic test env, a nested gate is still found by ancestry:
any held slot whose recorded owner is an ancestor. A value naming a pid that is not an ancestor is
ignored. Not a setting.
