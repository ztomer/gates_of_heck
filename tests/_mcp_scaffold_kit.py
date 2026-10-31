"""Shared plumbing for the two mcp_scaffold suites.

`lib/mcp_scaffold.py` is exercised two ways — in process through handle_message, and over a real
stdio pipe — and both need the same import path and the same path to the demo server. It lives in
one place so the suites share it rather than each carrying a copy that can drift.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER = REPO_ROOT / "lib" / "mcp_scaffold.py"

sys.path.insert(0, str(REPO_ROOT))

from lib import mcp_scaffold as mc  # noqa: E402
