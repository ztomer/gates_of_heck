# GOH_MIN_FREE_GIB
Area: pushes
Kind: setting
Order: 780
Default: unset (not checked)

GiB a cold gate needs free. `push_gate.sh` refuses the push before it makes the export, and
`round.sh` before it commits, naming the free space and the need (`lib/preflight_disk.py`) -- a full
disk failed a private Swift app's pushes as codesign and sanitizer errors. Measure your cold build
and add a margin.
