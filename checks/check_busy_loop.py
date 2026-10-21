#!/usr/bin/env python3
"""`goh busy-loop` -- a tracked shell file that runs a backgrounded busy loop is refused.

A BY-PATH ENTRY POINT, not a Python checker: the gate is native
(`crates/goh/src/busyloop.rs`), and this file exists so a consumer that calls
`python3 $GOH_DIR/checks/check_busy_loop.py` gets the same answer as one that
calls `bash $GOH_DIR/gates/goh.sh busy-loop` -- the one binary resolution, the
same arguments, the same exit code. The rule it enforces is in
`docs/contracts.md`; the scope it measures and the shape it judges are in the
native module's own docstring.
"""

import os
import sys

GOH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gates", "goh.sh")
sys.stdout.flush()
os.execvp("bash", ["bash", GOH, "busy-loop", *sys.argv[1:]])
