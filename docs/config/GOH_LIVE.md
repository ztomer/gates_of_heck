# GOH_LIVE
Area: gate-source
Kind: setting
Order: 10
Default: unset (consumers run HEAD)

`1` runs the gates from the shared checkout's WORKING TREE. Unset, every entry point
(`structural.sh`, `rust_gate.sh`, `push_gate.sh`, ...) and sourced lib (`_common.sh`, `tui/lib.sh`)
re-runs itself from an immutable export of the checkout's HEAD (`gates/_from_head.sh`, C4), so an
uncommitted edit to gate source cannot judge any consumer's commit. Set it to develop the gates;
this repo's suite sets it (`tests/conftest.py`), and a gate started under pytest without it refuses.
The native binary follows: see `GOH_LIVE_TARGET_DIR`.
