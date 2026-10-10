#!/usr/bin/env python3
"""Retired: this check is `goh subprocess-stdin`. Kept so a repo calling this file by path keeps
working; call `$GOH_DIR/gates/goh.sh subprocess-stdin` instead. It has no entry in
tests/reference_kit.py and needs none: unlike its siblings it has no retired Python behind it. The
rule, the incident and the ratchet are in crates/goh/src/substdin/; the specification is the test
table beside it."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
__import__("_retired").forward("subprocess-stdin", __name__)
