#!/usr/bin/env python3
"""`goh js-syntax` -- every tracked JavaScript source in this repository parses.

A BY-PATH ENTRY POINT, not a Python checker: the gate is native
(`crates/goh/src/jssyntax/`), and this file exists so a consumer that calls
`python3 $GOH_DIR/checks/check_js_syntax.py` gets the same answer as one that
calls `bash $GOH_DIR/gates/goh.sh js-syntax` -- the one binary resolution, the
same arguments, the same exit code. The rule it enforces is in
`docs/contracts.md`; the scope it measures and the template rule are in the
native module's own docstring.
"""

import os
import sys

GOH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gates", "goh.sh")
sys.stdout.flush()
os.execvp("bash", ["bash", GOH, "js-syntax", *sys.argv[1:]])
