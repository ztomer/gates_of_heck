#!/usr/bin/env python3
"""Retired (Phase N3): this check is `goh emoji`. Kept only so a repo calling this file by path keeps
working; call `$GOH_DIR/gates/goh.sh emoji` instead. The behaviour it had is the frozen spec in
tests/reference_kit.py.

Forwarder (Phase N3): execs `gates/goh.sh emoji` with the same arguments, for a consumer that calls
it by path; imported, it raises. Pinned by `test_retired_shims.py`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
__import__("_retired").forward("emoji", __name__)
