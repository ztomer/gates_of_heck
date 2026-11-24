"""The small stores as a test reads them back (GOH.STATEDBS3): a store's record is goh.db's row
beside its file (lib/small_stores.py), so a test reads it through the seam, never the file."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import estate_registry  # lib/, on the path from the line above


def estate_state(state: Path) -> dict:
    """The estate state STATE names, as every reader sees it ({} for none)."""
    return estate_registry.read_state(Path(state))
