#!/usr/bin/env python3
"""Retired (Phase N3): this check is `goh home-paths`. Kept so a repo calling this file by path keeps
working; call `$GOH_DIR/gates/goh.sh home-paths` instead. The behaviour it had is the frozen spec in
tests/reference_kit.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
__import__("_retired").forward("home-paths", __name__)
