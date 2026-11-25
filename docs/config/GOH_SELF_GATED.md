# GOH_SELF_GATED
Area: runtime
Kind: internal
Order: 310
Default: set by `gates/_self_gate.sh`

`<tree>|<toplevel>`: the staged tree a goh commit was handed to and the checkout it is committed in
(GOH.SELFGATE), so the hand-off happens once and the export runs that tree's `tools/gate.sh`. Not a
setting.
